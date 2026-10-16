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

constexpr UINT32 kWidth = 640;
constexpr UINT32 kHeight = 400;
constexpr std::size_t kPixelCount = static_cast<std::size_t>(kWidth) * kHeight;
constexpr std::size_t kRawBytes = kPixelCount * sizeof(std::uint16_t);

struct Options {
    std::filesystem::path output_prefix;
    std::filesystem::path q_file;
    double disparity_scale = 0.0;
    int frame_count = 1;
    bool list_modes = false;
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
        } else {
            throw std::runtime_error("Unknown command-line option.");
        }
    }
    if (options.list_modes) return options;
    if (options.output_prefix.empty() || options.q_file.empty() || options.disparity_scale <= 0.0 ||
        !std::isfinite(options.disparity_scale) || options.frame_count < 1 || options.frame_count > 1000) {
        throw std::runtime_error("--out-prefix, --q-file and a positive --disparity-scale are required.");
    }
    return options;
}

bool IsY16(REFGUID subtype);
ComPtr<IMFActivate> FindDepthCamera();

void ListNativeModes() {
    ComPtr<IMFActivate> activation = FindDepthCamera();
    ComPtr<IMFMediaSource> source;
    CheckHr(activation->ActivateObject(IID_PPV_ARGS(&source)), "Activate depth camera");
    ComPtr<IMFSourceReader> reader;
    CheckHr(MFCreateSourceReaderFromMediaSource(source.Get(), nullptr, &reader), "Create mode-list reader");
    for (DWORD i = 0; i < 64; ++i) {
        ComPtr<IMFMediaType> type;
        const HRESULT hr = reader->GetNativeMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, i, &type);
        if (hr == MF_E_NO_MORE_TYPES) break;
        CheckHr(hr, "Enumerate native media type");
        GUID subtype{}; UINT32 width = 0, height = 0; UINT32 numerator = 0, denominator = 0;
        CheckHr(type->GetGUID(MF_MT_SUBTYPE, &subtype), "Read media subtype");
        CheckHr(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height), "Read media dimensions");
        const HRESULT rate_hr = MFGetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, &numerator, &denominator);
        std::wcout << L"mode " << i << L": " << width << L"x" << height << L", subtype="
                   << subtype.Data1 << L", fps=";
        if (SUCCEEDED(rate_hr) && denominator) std::wcout << static_cast<double>(numerator) / denominator;
        else std::wcout << L"unknown";
        std::wcout << (IsY16(subtype) ? L", Y16" : L"") << L"\n";
    }
    source->Shutdown();
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

std::vector<std::vector<std::uint8_t>> CaptureY16Frames(int frame_count, std::vector<LONGLONG>& timestamps) {
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
        if (IsY16(subtype) && width == kWidth && height == kHeight) {
            y16_type = type;
            break;
        }
    }
    if (!y16_type) {
        throw std::runtime_error("DepthCam does not expose a 640x400 Y16 media type.");
    }
    CheckHr(reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, y16_type.Get()),
            "Select native Y16 media type");

    std::vector<std::vector<std::uint8_t>> frames;
    while (static_cast<int>(frames.size()) < frame_count) {
        DWORD stream_index = 0;
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        CheckHr(reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &stream_index, &flags,
                                   &timestamp, &sample),
                "Read Y16 frame");
        if (!sample) {
            continue;
        }

        ComPtr<IMFMediaBuffer> buffer;
        CheckHr(sample->ConvertToContiguousBuffer(&buffer), "Create contiguous Y16 buffer");
        BYTE* data = nullptr;
        DWORD maximum_length = 0;
        DWORD current_length = 0;
        CheckHr(buffer->Lock(&data, &maximum_length, &current_length), "Lock Y16 buffer");
        std::vector<std::uint8_t> result;
        if (current_length == kRawBytes) {
            result.assign(data, data + current_length);
        }
        buffer->Unlock();
        if (result.empty()) {
            throw std::runtime_error("Y16 buffer size was not 640x400x2 bytes; refusing to guess row stride.");
        }
        timestamps.push_back(timestamp);
        frames.push_back(std::move(result));
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
                                            const std::array<double, 16>& q,
                                            double disparity_scale) {
    std::vector<std::array<double, 3>> points;
    points.reserve(kPixelCount);
    for (UINT32 y = 0; y < kHeight; ++y) {
        for (UINT32 x = 0; x < kWidth; ++x) {
            const std::size_t offset = (static_cast<std::size_t>(y) * kWidth + x) * 2;
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
               << "  \"width\": " << kWidth << ",\n"
               << "  \"height\": " << kHeight << ",\n"
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
               const std::vector<LONGLONG>& timestamps, double elapsed_seconds) {
    if (frames.size() <= 1) return;
    auto raw_path = options.output_prefix; raw_path += L"_burst.raw";
    auto csv_path = options.output_prefix; csv_path += L"_timestamps.csv";
    WriteNewFile(raw_path, [&](std::ofstream& out) {
        for (const auto& frame : frames)
            out.write(reinterpret_cast<const char*>(frame.data()), static_cast<std::streamsize>(frame.size()));
    });
    WriteNewFile(csv_path, [&](std::ofstream& out) {
        out << "index,mf_timestamp_100ns,elapsed_seconds\n" << std::fixed << std::setprecision(6);
        for (std::size_t i = 0; i < frames.size(); ++i)
            out << i << ',' << timestamps[i] << ',' << (timestamps[i] - timestamps[0]) / 10000000.0 << '\n';
    });
    const double timestamp_span = frames.size() > 1
        ? static_cast<double>(timestamps.back() - timestamps.front()) / 10000000.0 : 0.0;
    std::cout << "Burst: " << frames.size() << " frames in " << elapsed_seconds << " s ("
              << frames.size() / elapsed_seconds << " fps wall-clock); MF timestamp rate "
              << (timestamp_span > 0 ? (frames.size() - 1) / timestamp_span : 0) << " fps\n";
    std::wcout << L"Burst raw: " << raw_path.wstring() << L"\nTimestamps: " << csv_path.wstring() << L"\n";
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        const Options options = ParseOptions(argc, argv);
        MediaFoundationRuntime runtime;
        if (options.list_modes) { ListNativeModes(); return 0; }
        const auto q = ReadQMatrix(options.q_file);
        std::vector<LONGLONG> timestamps;
        const auto started = std::chrono::steady_clock::now();
        const auto frames = CaptureY16Frames(options.frame_count, timestamps);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        const auto& raw = frames.front();
        const auto points = Reproject(raw, q, options.disparity_scale);
        if (points.size() < 100) {
            throw std::runtime_error("Fewer than 100 valid points were reconstructed; no output was published.");
        }
        SaveOutputs(options, raw, points);
        SaveBurst(options, frames, timestamps, elapsed);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Capture failed: " << error.what() << "\n";
        return 1;
    }
}
