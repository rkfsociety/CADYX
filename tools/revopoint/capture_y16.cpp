#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <wrl/client.h>

#include <cmath>
#include <array>
#include <chrono>
#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

struct Options {
    std::filesystem::path output_prefix;
    std::filesystem::path q_file;
    double disparity_scale = 0.0;
    int frame_count = 1;
    int requested_fps = 15;
    UINT32 width = 640;
    UINT32 height = 400;
    std::filesystem::path live_raw_path;
    std::filesystem::path ready_file;
    std::filesystem::path stop_file;
    bool list_modes = false;
    bool measure_only = false;
};

std::string Hresult(HRESULT hr);

struct MediaFoundationRuntime {
    MediaFoundationRuntime() {
        const HRESULT com_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (FAILED(com_result)) {
            throw std::runtime_error("CoInitializeEx failed: " + Hresult(com_result));
        }
        initialized_com = true;
        const HRESULT mf_result = MFStartup(MF_VERSION);
        if (FAILED(mf_result)) {
            throw std::runtime_error("MFStartup failed: " + Hresult(mf_result));
        }
        initialized_mf = true;
    }

    ~MediaFoundationRuntime() {
        if (initialized_mf) {
            MFShutdown();
        }
        if (initialized_com) {
            CoUninitialize();
        }
    }

    bool initialized_com = false;
    bool initialized_mf = false;
};

std::string Hresult(HRESULT hr) {
    std::ostringstream output;
    output << "0x" << std::hex << std::uppercase << static_cast<unsigned long>(hr);
    return output.str();
}

void CheckHr(HRESULT hr, const char* action) {
    if (FAILED(hr)) {
        throw std::runtime_error(std::string(action) + " failed: " + Hresult(hr));
    }
}

void PrintUsage() {
    std::cout << "Capture Y16 frames and reproject the first frame through a supplied Q.bin.\n"
              << "Usage: CaptureY16.exe --out-prefix PATH --q-file PATH --disparity-scale NUMBER [--frames N]\n"
              << "       [--fps 5|10|15] [--width 640|1280] [--height 400|800]\n"
              << "       CaptureY16.exe --out-prefix PATH --frames N --fps 5|10|15 --width W --height H --measure-only\n"
              << "       Optional --ready-file PATH is created after the first valid frame is received.\n"
              << "       Optional --stop-file PATH ends a burst after the next valid frame.\n"
              << "       CaptureY16.exe --list-modes\n"
              << "The scale is explicit because the raw-to-disparity mapping is not calibrated yet.\n";
}

Options ParseOptions(int argc, wchar_t** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::wstring argument(argv[i]);
        if (argument == L"--help" || argument == L"-h") {
            PrintUsage();
            std::exit(0);
        }
        if (argument == L"--list-modes") { options.list_modes = true; continue; }
        if (argument == L"--measure-only") { options.measure_only = true; continue; }
        if (i + 1 >= argc) {
            throw std::runtime_error("Missing value after a command-line option.");
        }
        const std::wstring value(argv[++i]);
        if (argument == L"--out-prefix") {
            options.output_prefix = value;
        } else if (argument == L"--q-file") {
            options.q_file = value;
        } else if (argument == L"--disparity-scale") {
            try {
                options.disparity_scale = std::stod(value);
            } catch (...) {
                throw std::runtime_error("--disparity-scale must be a positive number.");
            }
        } else if (argument == L"--frames") {
            try { options.frame_count = std::stoi(value); }
            catch (...) { throw std::runtime_error("--frames must be an integer from 1 to 1000."); }
        } else if (argument == L"--fps") {
            try { options.requested_fps = std::stoi(value); }
            catch (...) { throw std::runtime_error("--fps must be 5, 10, or 15."); }
        } else if (argument == L"--width") {
            try { options.width = static_cast<UINT32>(std::stoul(value)); }
            catch (...) { throw std::runtime_error("--width must be 640 or 1280."); }
        } else if (argument == L"--height") {
            try { options.height = static_cast<UINT32>(std::stoul(value)); }
            catch (...) { throw std::runtime_error("--height must be 400 or 800."); }
        } else if (argument == L"--live-raw") {
            options.live_raw_path = value;
        } else if (argument == L"--ready-file") {
            options.ready_file = value;
        } else if (argument == L"--stop-file") {
            options.stop_file = value;
        } else {
            throw std::runtime_error("Unknown command-line option.");
        }
    }
    if (options.list_modes) return options;
    if (options.output_prefix.empty() || (!options.measure_only && (options.q_file.empty() || options.disparity_scale <= 0.0 ||
        !std::isfinite(options.disparity_scale))) || options.frame_count < 1 || options.frame_count > 1000 ||
        !((options.width == 640 && (options.height == 400 || options.height == 800)) ||
          (options.width == 1280 && options.height == 800)) ||
        (options.measure_only && options.frame_count < 2) ||
        (!options.measure_only && (options.width != 640 || options.height != 400)) ||
        (options.requested_fps != 5 && options.requested_fps != 10 && options.requested_fps != 15)) {
        throw std::runtime_error("Invalid output, dimensions, frame count, FPS, or reprojection arguments.");
    }
    return options;
}

bool IsY16(REFGUID subtype);
ComPtr<IMFActivate> FindDepthCamera();
std::wstring GetFriendlyName(IMFActivate* activation);

void ListNativeModes() {
    ComPtr<IMFAttributes> attributes;
    CheckHr(MFCreateAttributes(&attributes, 1), "Create mode-list attributes");
    CheckHr(attributes->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE,
                                MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID),
            "Set video-capture source type");
    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    CheckHr(MFEnumDeviceSources(attributes.Get(), &devices, &count), "Enumerate video cameras");
    bool listed = false;
    try {
        for (UINT32 device_index = 0; device_index < count; ++device_index) {
            const std::wstring name = GetFriendlyName(devices[device_index]);
            if (name.rfind(L"DepthCam", 0) != 0 && name.rfind(L"RGBCam", 0) != 0) continue;
            listed = true;
            ComPtr<IMFMediaSource> source;
            CheckHr(devices[device_index]->ActivateObject(IID_PPV_ARGS(&source)), "Activate camera for mode listing");
            ComPtr<IMFSourceReader> reader;
            CheckHr(MFCreateSourceReaderFromMediaSource(source.Get(), nullptr, &reader), "Create mode-list reader");
            std::wcout << L"Camera: " << name << L"\n";
            for (DWORD mode = 0; mode < 64; ++mode) {
                ComPtr<IMFMediaType> type;
                const HRESULT hr = reader->GetNativeMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, mode, &type);
                if (hr == MF_E_NO_MORE_TYPES) break;
                CheckHr(hr, "Enumerate native media type");
                GUID subtype{}; UINT32 width = 0, height = 0; UINT32 numerator = 0, denominator = 0;
                CheckHr(type->GetGUID(MF_MT_SUBTYPE, &subtype), "Read media subtype");
                CheckHr(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height), "Read media dimensions");
                const HRESULT rate_hr = MFGetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, &numerator, &denominator);
                std::wcout << L"mode " << mode << L": " << width << L"x" << height << L", subtype="
                           << subtype.Data1 << L", fps=";
                if (SUCCEEDED(rate_hr) && denominator) std::wcout << static_cast<double>(numerator) / denominator;
                else std::wcout << L"unknown";
                std::wcout << (IsY16(subtype) ? L", Y16" : L"") << L"\n";
            }
            source->Shutdown();
        }
    } catch (...) {
        for (UINT32 i = 0; i < count; ++i) if (devices[i]) devices[i]->Release();
        CoTaskMemFree(devices);
        throw;
    }
    for (UINT32 i = 0; i < count; ++i) if (devices[i]) devices[i]->Release();
    CoTaskMemFree(devices);
    if (!listed) throw std::runtime_error("Neither a DepthCam nor an RGBCam UVC device was found.");
}

bool IsY16(REFGUID subtype) {
    return subtype.Data1 == 0x20363159 && subtype.Data2 == 0 && subtype.Data3 == 0x0010 &&
           subtype.Data4[0] == 0x80 && subtype.Data4[1] == 0 && subtype.Data4[2] == 0 &&
           subtype.Data4[3] == 0xAA && subtype.Data4[4] == 0 && subtype.Data4[5] == 0x38 &&
           subtype.Data4[6] == 0x9B && subtype.Data4[7] == 0x71;
}

std::wstring GetFriendlyName(IMFActivate* activation) {
    WCHAR* name = nullptr;
    UINT32 length = 0;
    CheckHr(activation->GetAllocatedString(MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME, &name, &length),
            "Get camera friendly name");
    std::wstring result(name, length);
    CoTaskMemFree(name);
    return result;
}

ComPtr<IMFActivate> FindDepthCamera() {
    ComPtr<IMFAttributes> attributes;
    CheckHr(MFCreateAttributes(&attributes, 1), "MFCreateAttributes");
    CheckHr(attributes->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE,
                                MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID),
            "Set video-capture source type");

    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    CheckHr(MFEnumDeviceSources(attributes.Get(), &devices, &count), "MFEnumDeviceSources");
    ComPtr<IMFActivate> selected;
    std::wstring selected_name;
    for (UINT32 i = 0; i < count; ++i) {
        try {
            const std::wstring name = GetFriendlyName(devices[i]);
            if (name.rfind(L"DepthCam", 0) == 0) {
                if (selected) {
                    throw std::runtime_error("More than one DepthCam is present; disconnect extras before capture.");
                }
                selected.Attach(devices[i]);
                devices[i] = nullptr;
                selected_name = name;
            }
        } catch (...) {
            for (UINT32 j = 0; j < count; ++j) {
                if (devices[j]) {
                    devices[j]->Release();
                }
            }
            CoTaskMemFree(devices);
            throw;
        }
    }
    for (UINT32 i = 0; i < count; ++i) {
        if (devices[i]) {
            devices[i]->Release();
        }
    }
    CoTaskMemFree(devices);

    if (!selected) {
        throw std::runtime_error("No UVC camera whose name starts with DepthCam was found.");
    }
    std::wcout << L"Camera: " << selected_name << L"\n";
    return selected;
}

std::vector<std::vector<std::uint8_t>> CaptureY16Frames(int frame_count, int requested_fps,
                                                        UINT32 requested_width, UINT32 requested_height,
                                                        std::vector<LONGLONG>& timestamps,
                                                        std::vector<std::int64_t>& utc_epoch_us,
                                                        std::vector<DWORD>& sample_flags,
                                                        std::vector<DWORD>& empty_sample_flags,
                                                        std::size_t& null_sample_count,
                                                        const std::filesystem::path& live_raw_path,
                                                        const std::filesystem::path& ready_file,
                                                        const std::filesystem::path& stop_file) {
    ComPtr<IMFActivate> activation = FindDepthCamera();
    ComPtr<IMFMediaSource> source;
    CheckHr(activation->ActivateObject(IID_PPV_ARGS(&source)), "Activate depth camera");

    ComPtr<IMFAttributes> reader_attributes;
    CheckHr(MFCreateAttributes(&reader_attributes, 1), "Create Source Reader attributes");
    CheckHr(reader_attributes->SetUINT32(MF_READWRITE_DISABLE_CONVERTERS, TRUE),
            "Disable Source Reader converters");

    ComPtr<IMFSourceReader> reader;
    CheckHr(MFCreateSourceReaderFromMediaSource(source.Get(), reader_attributes.Get(), &reader),
            "Create Media Foundation Source Reader");

    ComPtr<IMFMediaType> y16_type;
    for (DWORD i = 0; i < 64; ++i) {
        ComPtr<IMFMediaType> type;
        const HRESULT hr = reader->GetNativeMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, i, &type);
        if (hr == MF_E_NO_MORE_TYPES) {
            break;
        }
        CheckHr(hr, "Get native media type");

        GUID subtype{};
        UINT32 width = 0;
        UINT32 height = 0;
        CheckHr(type->GetGUID(MF_MT_SUBTYPE, &subtype), "Read media subtype");
        CheckHr(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height), "Read media dimensions");
        UINT32 rate_num = 0, rate_den = 0;
        const HRESULT rate_hr = MFGetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, &rate_num, &rate_den);
        if (IsY16(subtype) && width == requested_width && height == requested_height && SUCCEEDED(rate_hr) && rate_den &&
            std::abs(static_cast<double>(rate_num) / rate_den - requested_fps) < 0.1) y16_type = type;
    }
    if (!y16_type) {
        throw std::runtime_error("DepthCam does not expose the requested Y16 dimensions and frame-rate mode.");
    }
    CheckHr(reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, y16_type.Get()),
            "Select native Y16 media type");

    const std::size_t raw_bytes = static_cast<std::size_t>(requested_width) * requested_height * sizeof(std::uint16_t);
    std::vector<std::vector<std::uint8_t>> frames;
    while (static_cast<int>(frames.size()) < frame_count &&
           (frames.empty() || stop_file.empty() || !std::filesystem::exists(stop_file))) {
        DWORD stream_index = 0;
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        CheckHr(reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &stream_index, &flags,
                                   &timestamp, &sample),
                "Read Y16 frame");
        if (!sample) {
            ++null_sample_count;
            empty_sample_flags.push_back(flags);
            continue;
        }

        ComPtr<IMFMediaBuffer> buffer;
        CheckHr(sample->ConvertToContiguousBuffer(&buffer), "Create contiguous Y16 buffer");
        BYTE* data = nullptr;
        DWORD maximum_length = 0;
        DWORD current_length = 0;
        CheckHr(buffer->Lock(&data, &maximum_length, &current_length), "Lock Y16 buffer");
        std::vector<std::uint8_t> result;
        if (current_length == raw_bytes) {
            result.assign(data, data + current_length);
        }
        buffer->Unlock();
        if (result.empty()) {
            throw std::runtime_error("Y16 buffer size did not match the selected dimensions; refusing to guess row stride.");
        }
        timestamps.push_back(timestamp);
        sample_flags.push_back(flags);
        utc_epoch_us.push_back(std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
        frames.push_back(std::move(result));
        if (!live_raw_path.empty()) {
            if (!live_raw_path.parent_path().empty()) std::filesystem::create_directories(live_raw_path.parent_path());
            auto temporary = live_raw_path; temporary += L".tmp";
            { std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
              out.write(reinterpret_cast<const char*>(frames.back().data()), static_cast<std::streamsize>(frames.back().size()));
              if (!out) throw std::runtime_error("Could not update live frame file."); }
            bool published = false;
            for (int attempt = 0; attempt < 20; ++attempt) {
                if (MoveFileExW(temporary.c_str(), live_raw_path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                    published = true;
                    break;
                }
                const DWORD error = GetLastError();
                if (error != ERROR_SHARING_VIOLATION && error != ERROR_LOCK_VIOLATION) break;
                Sleep(10);
            }
            if (!published) throw std::runtime_error("Could not atomically publish live frame file.");
        }
        if (frames.size() == 1 && !ready_file.empty()) {
            if (!ready_file.parent_path().empty()) std::filesystem::create_directories(ready_file.parent_path());
            HANDLE file = CreateFileW(ready_file.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                      FILE_ATTRIBUTE_NORMAL, nullptr);
            if (file == INVALID_HANDLE_VALUE)
                throw std::runtime_error("Could not create the first-frame ready signal.");
            const char ready[] = "first Y16 frame received\n";
            DWORD written = 0;
            const BOOL success = WriteFile(file, ready, static_cast<DWORD>(sizeof(ready) - 1), &written, nullptr);
            CloseHandle(file);
            if (!success || written != sizeof(ready) - 1) {
                DeleteFileW(ready_file.c_str());
                throw std::runtime_error("Could not write the first-frame ready signal.");
            }
        }
    }
    return frames;
}

std::array<double, 16> ReadQMatrix(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) {
        throw std::runtime_error("Could not open Q calibration file.");
    }
    if (input.tellg() != static_cast<std::streamoff>(sizeof(float) * 16)) {
        throw std::runtime_error("Q calibration file must contain exactly 16 little-endian float32 values (64 bytes).");
    }
    input.seekg(0);
    std::array<float, 16> values{};
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(sizeof(values)));
    if (!input) {
        throw std::runtime_error("Could not read Q calibration matrix.");
    }
    std::array<double, 16> result{};
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (!std::isfinite(values[i])) {
            throw std::runtime_error("Q calibration matrix contains a non-finite value.");
        }
        result[i] = values[i];
    }
    return result;
}

std::vector<std::array<double, 3>> Reproject(const std::vector<std::uint8_t>& raw,
                                            UINT32 width, UINT32 height,
                                            const std::array<double, 16>& q,
                                            double disparity_scale) {
    std::vector<std::array<double, 3>> points;
    points.reserve(static_cast<std::size_t>(width) * height);
    for (UINT32 y = 0; y < height; ++y) {
        for (UINT32 x = 0; x < width; ++x) {
            const std::size_t offset = (static_cast<std::size_t>(y) * width + x) * 2;
            const std::uint16_t pixel = static_cast<std::uint16_t>(raw[offset] | (raw[offset + 1] << 8));
            if (pixel == 0) {
                continue;
            }
            const double disparity = static_cast<double>(pixel) / disparity_scale;
            const double denominator = q[12] * x + q[13] * y + q[14] * disparity + q[15];
            if (!std::isfinite(denominator) || std::abs(denominator) < 1e-12) {
                continue;
            }
            const double px = (q[0] * x + q[1] * y + q[2] * disparity + q[3]) / denominator;
            const double py = (q[4] * x + q[5] * y + q[6] * disparity + q[7]) / denominator;
            const double pz = (q[8] * x + q[9] * y + q[10] * disparity + q[11]) / denominator;
            if (std::isfinite(px) && std::isfinite(py) && std::isfinite(pz)) {
                points.push_back({px, py, pz});
            }
        }
    }
    return points;
}

void WriteNewFile(const std::filesystem::path& path, const std::function<void(std::ofstream&)>& write) {
    if (std::filesystem::exists(path)) {
        throw std::runtime_error("Output already exists; choose a new prefix: " + path.string());
    }
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("Could not create output file: " + path.string());
    }
    write(output);
    output.close();
    if (!output) {
        throw std::runtime_error("Could not finish writing output file: " + path.string());
    }
}

void SaveOutputs(const Options& options, const std::vector<std::uint8_t>& raw,
                 const std::vector<std::array<double, 3>>& points) {
    const std::filesystem::path parent = options.output_prefix.parent_path();
    if (!parent.empty()) {
        std::filesystem::create_directories(parent);
    }
    std::filesystem::path raw_path = options.output_prefix;
    raw_path += L".raw";
    std::filesystem::path ply_path = options.output_prefix;
    ply_path += L".ply";
    std::filesystem::path metadata_path = options.output_prefix;
    metadata_path += L".json";

    for (const auto& path : {raw_path, ply_path, metadata_path}) {
        if (std::filesystem::exists(path)) {
            throw std::runtime_error("An output already exists; no files were overwritten: " + path.string());
        }
    }

    WriteNewFile(raw_path, [&](std::ofstream& output) {
        output.write(reinterpret_cast<const char*>(raw.data()), static_cast<std::streamsize>(raw.size()));
    });
    WriteNewFile(ply_path, [&](std::ofstream& output) {
        output << "ply\nformat ascii 1.0\n"
               << "comment Q reprojection from the supplied Revo Scan calibration file\n"
               << "comment disparity_scale=" << std::setprecision(12) << options.disparity_scale
               << " (provisional; validate with a known dimension)\n"
               << "element vertex " << points.size() << "\n"
               << "property float x\nproperty float y\nproperty float z\nend_header\n";
        output << std::fixed << std::setprecision(6);
        for (const auto& point : points) {
            output << point[0] << ' ' << point[1] << ' ' << point[2] << '\n';
        }
    });
    WriteNewFile(metadata_path, [&](std::ofstream& output) {
        output << std::fixed << std::setprecision(12)
               << "{\n"
               << "  \"camera\": \"DepthCam\",\n"
               << "  \"width\": " << options.width << ",\n"
               << "  \"height\": " << options.height << ",\n"
               << "  \"raw_format\": \"Y16 little-endian\",\n"
               << "  \"raw_file\": \"" << raw_path.filename().string() << "\",\n"
               << "  \"ply_file\": \"" << ply_path.filename().string() << "\",\n"
               << "  \"calibration_file\": \"" << options.q_file.filename().string() << "\",\n"
               << "  \"disparity_scale\": " << options.disparity_scale << ",\n"
               << "  \"disparity_scale_status\": \"provisional_unverified\",\n"
               << "  \"point_count\": " << points.size() << ",\n"
               << "  \"coordinates_unit\": \"inherited_from_Q.bin; not validated against a known dimension\"\n"
               << "}\n";
    });
    std::wcout << L"Raw frame: " << raw_path.wstring() << L"\n"
               << L"Point cloud: " << ply_path.wstring() << L" (" << points.size() << L" points)\n"
               << L"Metadata: " << metadata_path.wstring() << L"\n";
}

void SaveBurst(const Options& options, const std::vector<std::vector<std::uint8_t>>& frames,
               const std::vector<LONGLONG>& timestamps, const std::vector<std::int64_t>& utc_epoch_us,
               const std::vector<DWORD>& sample_flags, const std::vector<DWORD>& empty_sample_flags,
               std::size_t null_sample_count,
               double elapsed_seconds) {
    if (frames.size() <= 1) return;
    auto raw_path = options.output_prefix; raw_path += L"_burst.raw";
    auto csv_path = options.output_prefix; csv_path += L"_timestamps.csv";
    WriteNewFile(raw_path, [&](std::ofstream& out) {
        for (const auto& frame : frames)
            out.write(reinterpret_cast<const char*>(frame.data()), static_cast<std::streamsize>(frame.size()));
    });
    WriteNewFile(csv_path, [&](std::ofstream& out) {
        out << "index,mf_timestamp_100ns,elapsed_seconds,utc_epoch_us,sample_flags\n" << std::fixed << std::setprecision(6);
        for (std::size_t i = 0; i < frames.size(); ++i)
            out << i << ',' << timestamps[i] << ',' << (timestamps[i] - timestamps[0]) / 10000000.0
                << ',' << utc_epoch_us[i] << ',' << sample_flags[i] << '\n';
    });
    const double timestamp_span = frames.size() > 1
        ? static_cast<double>(timestamps.back() - timestamps.front()) / 10000000.0 : 0.0;
    std::cout << "Burst: " << frames.size() << " frames in " << elapsed_seconds << " s ("
              << frames.size() / elapsed_seconds << " fps wall-clock); MF timestamp rate "
              << (timestamp_span > 0 ? (frames.size() - 1) / timestamp_span : 0) << " fps\n";
    std::cout << "ReadSample calls without a frame: " << null_sample_count << "\n";
    if (!empty_sample_flags.empty()) {
        std::cout << "No-frame flags:";
        for (const DWORD flags : empty_sample_flags) std::cout << " 0x" << std::hex << flags << std::dec;
        std::cout << "\n";
    }
    std::wcout << L"Burst raw: " << raw_path.wstring() << L"\nTimestamps: " << csv_path.wstring() << L"\n";
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        const Options options = ParseOptions(argc, argv);
        if (!options.ready_file.empty() && std::filesystem::exists(options.ready_file))
            throw std::runtime_error("Ready signal already exists; refusing to overwrite it.");
        if (!options.stop_file.empty() && std::filesystem::exists(options.stop_file))
            throw std::runtime_error("Stop signal already exists; refusing to start a stopped capture.");
        MediaFoundationRuntime runtime;
        if (options.list_modes) { ListNativeModes(); return 0; }
        std::array<double, 16> q{};
        if (!options.measure_only) q = ReadQMatrix(options.q_file);
        std::vector<LONGLONG> timestamps;
        std::vector<std::int64_t> utc_epoch_us;
        std::vector<DWORD> sample_flags;
        std::vector<DWORD> empty_sample_flags;
        std::size_t null_sample_count = 0;
        const auto started = std::chrono::steady_clock::now();
        const auto frames = CaptureY16Frames(options.frame_count, options.requested_fps,
                                             options.width, options.height, timestamps, utc_epoch_us,
                                             sample_flags, empty_sample_flags, null_sample_count,
                                             options.live_raw_path, options.ready_file, options.stop_file);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        if (!options.measure_only) {
            const auto& raw = frames.front();
            const auto points = Reproject(raw, options.width, options.height, q, options.disparity_scale);
            if (points.size() < 100) {
                throw std::runtime_error("Fewer than 100 valid points were reconstructed; no output was published.");
            }
            SaveOutputs(options, raw, points);
        }
        SaveBurst(options, frames, timestamps, utc_epoch_us, sample_flags,
                  empty_sample_flags, null_sample_count, elapsed);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Capture failed: " << error.what() << "\n";
        return 1;
    }
}
