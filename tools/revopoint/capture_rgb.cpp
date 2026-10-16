#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <wrl/client.h>

#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

constexpr UINT32 kWidth = 1280;
constexpr UINT32 kHeight = 800;
constexpr UINT32 kMjpgFourCc = 0x47504A4D;
constexpr UINT32 kNv12FourCc = 0x3231564E;

struct Options {
    std::filesystem::path output_prefix;
    int frame_count = 1;
    int requested_fps = 30;
    UINT32 requested_subtype = kMjpgFourCc;
};

std::string Hresult(HRESULT hr) {
    std::ostringstream output;
    output << "0x" << std::hex << std::uppercase << static_cast<unsigned long>(hr);
    return output.str();
}

void CheckHr(HRESULT hr, const char* action) {
    if (FAILED(hr)) throw std::runtime_error(std::string(action) + " failed: " + Hresult(hr));
}

struct Runtime {
    Runtime() {
        CheckHr(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
        com = true;
        CheckHr(MFStartup(MF_VERSION), "MFStartup");
        mf = true;
    }
    ~Runtime() {
        if (mf) MFShutdown();
        if (com) CoUninitialize();
    }
    bool com = false;
    bool mf = false;
};

std::wstring FriendlyName(IMFActivate* activation) {
    WCHAR* value = nullptr;
    UINT32 length = 0;
    CheckHr(activation->GetAllocatedString(MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME, &value, &length),
            "Get RGB camera name");
    std::wstring result(value, length);
    CoTaskMemFree(value);
    return result;
}

ComPtr<IMFActivate> FindRgbCamera() {
    ComPtr<IMFAttributes> attributes;
    CheckHr(MFCreateAttributes(&attributes, 1), "Create camera enumeration attributes");
    CheckHr(attributes->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE,
                                MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID),
            "Set camera source type");
    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    CheckHr(MFEnumDeviceSources(attributes.Get(), &devices, &count), "Enumerate cameras");
    ComPtr<IMFActivate> selected;
    try {
        for (UINT32 i = 0; i < count; ++i) {
            const std::wstring name = FriendlyName(devices[i]);
            if (name.rfind(L"RGBCam", 0) != 0) continue;
            if (selected) throw std::runtime_error("More than one RGBCam is present.");
            selected.Attach(devices[i]);
            devices[i] = nullptr;
            std::wcout << L"Camera: " << name << L"\n";
        }
    } catch (...) {
        for (UINT32 i = 0; i < count; ++i) if (devices[i]) devices[i]->Release();
        CoTaskMemFree(devices);
        throw;
    }
    for (UINT32 i = 0; i < count; ++i) if (devices[i]) devices[i]->Release();
    CoTaskMemFree(devices);
    if (!selected) throw std::runtime_error("No UVC camera whose name starts with RGBCam was found.");
    return selected;
}

Options ParseOptions(int argc, wchar_t** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::wstring arg(argv[i]);
        if (arg == L"--help" || arg == L"-h") {
            std::cout << "Capture frames from the Revopoint RGBCam UVC interface.\n"
                      << "Usage: CaptureRgb.exe --out-prefix PATH [--frames N] [--fps 30|20|15|10] [--format mjpg|nv12]\n";
            std::exit(0);
        }
        if (i + 1 >= argc) throw std::runtime_error("Missing value after a command-line option.");
        const std::wstring value(argv[++i]);
        try {
            if (arg == L"--out-prefix") options.output_prefix = value;
            else if (arg == L"--frames") options.frame_count = std::stoi(value);
            else if (arg == L"--fps") options.requested_fps = std::stoi(value);
            else if (arg == L"--format") {
                if (value == L"mjpg") options.requested_subtype = kMjpgFourCc;
                else if (value == L"nv12") options.requested_subtype = kNv12FourCc;
                else throw std::runtime_error("Format must be mjpg or nv12.");
            }
            else throw std::runtime_error("Unknown command-line option.");
        } catch (const std::invalid_argument&) {
            throw std::runtime_error("Invalid command-line option value.");
        } catch (const std::out_of_range&) {
            throw std::runtime_error("Command-line option value is out of range.");
        }
    }
    if (options.output_prefix.empty() || options.frame_count < 1 || options.frame_count > 1000 ||
        (options.requested_fps != 30 && options.requested_fps != 20 &&
         options.requested_fps != 15 && options.requested_fps != 10)) {
        throw std::runtime_error("--out-prefix, frames (1-1000), and fps (30|20|15|10) are required.");
    }
    return options;
}

void WriteNewFile(const std::filesystem::path& path, const std::vector<BYTE>& bytes) {
    if (std::filesystem::exists(path)) throw std::runtime_error("Output already exists: " + path.string());
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("Could not create output: " + path.string());
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    out.close();
    if (!out) throw std::runtime_error("Could not finish output: " + path.string());
}

int Run(const Options& options) {
    ComPtr<IMFActivate> activation = FindRgbCamera();
    ComPtr<IMFMediaSource> source;
    CheckHr(activation->ActivateObject(IID_PPV_ARGS(&source)), "Activate RGBCam");
    ComPtr<IMFAttributes> readerAttributes;
    CheckHr(MFCreateAttributes(&readerAttributes, 1), "Create Source Reader attributes");
    CheckHr(readerAttributes->SetUINT32(MF_READWRITE_DISABLE_CONVERTERS, TRUE), "Disable format conversion");
    ComPtr<IMFSourceReader> reader;
    CheckHr(MFCreateSourceReaderFromMediaSource(source.Get(), readerAttributes.Get(), &reader), "Create Source Reader");

    ComPtr<IMFMediaType> selectedType;
    for (DWORD i = 0; i < 64; ++i) {
        ComPtr<IMFMediaType> type;
        const HRESULT hr = reader->GetNativeMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, i, &type);
        if (hr == MF_E_NO_MORE_TYPES) break;
        CheckHr(hr, "Read native RGB media type");
        GUID subtype{}; UINT32 width = 0, height = 0, numerator = 0, denominator = 0;
        CheckHr(type->GetGUID(MF_MT_SUBTYPE, &subtype), "Read RGB subtype");
        CheckHr(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height), "Read RGB dimensions");
        const HRESULT rateHr = MFGetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, &numerator, &denominator);
        if (subtype.Data1 == options.requested_subtype && width == kWidth && height == kHeight &&
            SUCCEEDED(rateHr) && denominator &&
            std::abs(static_cast<double>(numerator) / denominator - options.requested_fps) < 0.1) {
            selectedType = type;
        }
    }
    if (!selectedType) throw std::runtime_error("RGBCam does not expose the requested native format and fps.");
    CheckHr(reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, selectedType.Get()),
            "Select native RGB mode");
    std::cout << "Selected format " << (options.requested_subtype == kMjpgFourCc ? "MJPG" : "NV12")
              << " at " << options.requested_fps << " fps; waiting for samples.\n" << std::flush;

    const auto outputDirectory = options.output_prefix.parent_path();
    if (!outputDirectory.empty()) std::filesystem::create_directories(outputDirectory);
    const auto csvPath = std::filesystem::path(options.output_prefix.wstring() + L"_timestamps.csv");
    if (std::filesystem::exists(csvPath)) throw std::runtime_error("Timestamp output already exists: " + csvPath.string());
    std::ofstream csv(csvPath);
    if (!csv) throw std::runtime_error("Could not create timestamp CSV.");
    csv << "index,mf_timestamp_100ns,utc_epoch_us,bytes\n";

    for (int frame = 0; frame < options.frame_count; ++frame) {
        DWORD streamIndex = 0, flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        do {
            CheckHr(reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0,
                                       &streamIndex, &flags, &timestamp, &sample), "Read RGB frame");
            if (!sample && (flags & MF_SOURCE_READERF_ENDOFSTREAM))
                throw std::runtime_error("RGBCam ended the stream before all requested frames arrived.");
        } while (!sample);

        ComPtr<IMFMediaBuffer> buffer;
        CheckHr(sample->ConvertToContiguousBuffer(&buffer), "Create contiguous MJPG buffer");
        BYTE* data = nullptr;
        DWORD maxLength = 0, currentLength = 0;
        CheckHr(buffer->Lock(&data, &maxLength, &currentLength), "Lock MJPG buffer");
        std::vector<BYTE> bytes(data, data + currentLength);
        buffer->Unlock();
        std::wostringstream fileName;
        fileName << options.output_prefix.filename().wstring();
        if (options.frame_count > 1) fileName << L"_" << std::setw(4) << std::setfill(L'0') << frame;
        bool validJpeg = options.requested_subtype != kMjpgFourCc;
        if (options.requested_subtype == kMjpgFourCc && bytes.size() >= 4 &&
            bytes[0] == 0xFF && bytes[1] == 0xD8) {
            const BYTE eoi[] = {0xFF, 0xD9};
            const auto marker = std::search(bytes.begin() + 2, bytes.end(),
                                             std::begin(eoi), std::end(eoi));
            if (marker != bytes.end() - 1) {
                bytes.resize(static_cast<std::size_t>(marker - bytes.begin()) + 2);
                validJpeg = true;
            }
        }
        fileName << (options.requested_subtype == kMjpgFourCc
                         ? (validJpeg ? L".jpg" : L".invalid-mjpg.bin") : L".nv12");
        const auto imagePath = outputDirectory / fileName.str();
        WriteNewFile(imagePath, bytes);
        if (!validJpeg) {
            std::ostringstream detail;
            detail << "RGBCam MJPG sample is not framed as a JPEG: " << bytes.size() << " bytes; prefix=";
            for (std::size_t i = 0; i < std::min<std::size_t>(16, bytes.size()); ++i)
                detail << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(bytes[i]);
            detail << "; suffix=";
            const std::size_t start = bytes.size() > 16 ? bytes.size() - 16 : 0;
            for (std::size_t i = start; i < bytes.size(); ++i)
                detail << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(bytes[i]);
            throw std::runtime_error(detail.str());
        }
        const auto utc = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        csv << frame << ',' << timestamp << ',' << utc << ',' << bytes.size() << '\n';
        csv.flush();
        std::wcout << L"Frame " << frame << L": " << imagePath.wstring() << L" (" << bytes.size() << L" bytes)\n";
    }
    source->Shutdown();
    return 0;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        Runtime runtime;
        return Run(ParseOptions(argc, argv));
    } catch (const std::exception& error) {
        std::cerr << "RGB capture failed: " << error.what() << "\n";
        return 1;
    }
}
