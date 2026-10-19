#include <windows.h>
#include <dshow.h>
#include <wrl/client.h>

#include <iostream>
#include <stdexcept>
#include <string>

using Microsoft::WRL::ComPtr;

namespace {

std::wstring FriendlyName(IMoniker* moniker) {
    ComPtr<IPropertyBag> properties;
    if (FAILED(moniker->BindToStorage(nullptr, nullptr, IID_PPV_ARGS(&properties)))) return L"";
    VARIANT value;
    VariantInit(&value);
    std::wstring result;
    if (SUCCEEDED(properties->Read(L"FriendlyName", &value, nullptr)) && value.vt == VT_BSTR)
        result.assign(value.bstrVal, SysStringLen(value.bstrVal));
    VariantClear(&value);
    return result;
}

void PrintCameraControls(IAMCameraControl* controls) {
    if (!controls) {
        std::wcout << L"  IAMCameraControl: unavailable\n";
        return;
    }
    for (const auto property : {CameraControl_Exposure, CameraControl_Focus,
                                CameraControl_Iris, CameraControl_Zoom,
                                CameraControl_Pan, CameraControl_Tilt}) {
        long minimum = 0, maximum = 0, step = 0, defaultValue = 0, capabilities = 0;
        const HRESULT rangeHr = controls->GetRange(property, &minimum, &maximum, &step,
                                                    &defaultValue, &capabilities);
        if (FAILED(rangeHr)) continue;
        long value = 0, flags = 0;
        const HRESULT valueHr = controls->Get(property, &value, &flags);
        std::wcout << L"  camera-control " << static_cast<int>(property)
                   << L" range=" << minimum << L".." << maximum << L" step=" << step
                   << L" default=" << defaultValue << L" caps=0x" << std::hex << capabilities << std::dec;
        if (SUCCEEDED(valueHr))
            std::wcout << L" current=" << value << L" mode=" << ((flags & CameraControl_Flags_Manual) ? L"manual" : L"auto");
        std::wcout << L"\n";
    }
}

void PrintVideoProcAmp(IAMVideoProcAmp* controls) {
    if (!controls) {
        std::wcout << L"  IAMVideoProcAmp: unavailable\n";
        return;
    }
    for (const auto property : {VideoProcAmp_Brightness, VideoProcAmp_Contrast,
                                VideoProcAmp_Saturation, VideoProcAmp_Sharpness,
                                VideoProcAmp_Gamma, VideoProcAmp_ColorEnable,
                                VideoProcAmp_WhiteBalance, VideoProcAmp_BacklightCompensation,
                                VideoProcAmp_Gain}) {
        long minimum = 0, maximum = 0, step = 0, defaultValue = 0, capabilities = 0;
        const HRESULT rangeHr = controls->GetRange(property, &minimum, &maximum, &step,
                                                    &defaultValue, &capabilities);
        if (FAILED(rangeHr)) continue;
        long value = 0, flags = 0;
        const HRESULT valueHr = controls->Get(property, &value, &flags);
        std::wcout << L"  video-control " << static_cast<int>(property)
                   << L" range=" << minimum << L".." << maximum << L" step=" << step
                   << L" default=" << defaultValue << L" caps=0x" << std::hex << capabilities << std::dec;
        if (SUCCEEDED(valueHr))
            std::wcout << L" current=" << value << L" mode=" << ((flags & VideoProcAmp_Flags_Manual) ? L"manual" : L"auto");
        std::wcout << L"\n";
    }
}

int RunDevices() {
    HRESULT hr = S_OK;
    ComPtr<ICreateDevEnum> deviceEnumerator;
    hr = CoCreateInstance(CLSID_SystemDeviceEnum, nullptr, CLSCTX_INPROC_SERVER,
                          IID_PPV_ARGS(&deviceEnumerator));
    if (FAILED(hr)) {
        std::wcerr << L"Could not create DirectShow device enumerator.\n";
        return 1;
    }
    ComPtr<IEnumMoniker> cameras;
    hr = deviceEnumerator->CreateClassEnumerator(CLSID_VideoInputDeviceCategory, &cameras, 0);
    if (hr != S_OK) {
        std::wcerr << L"No DirectShow video-capture devices were enumerated.\n";
        return 1;
    }
    bool found = false;
    ComPtr<IMoniker> moniker;
    while (cameras->Next(1, &moniker, nullptr) == S_OK) {
        const std::wstring name = FriendlyName(moniker.Get());
        if (name.rfind(L"DepthCam", 0) != 0 && name.rfind(L"RGBCam", 0) != 0) {
            moniker.Reset();
            continue;
        }
        found = true;
        std::wcout << L"Camera: " << name << L"\n";
        ComPtr<IBaseFilter> filter;
        hr = moniker->BindToObject(nullptr, nullptr, IID_PPV_ARGS(&filter));
        if (FAILED(hr)) {
            std::wcout << L"  Could not bind capture filter: 0x" << std::hex << hr << std::dec << L"\n";
            moniker.Reset();
            continue;
        }
        ComPtr<IAMCameraControl> cameraControl;
        filter.As(&cameraControl);
        PrintCameraControls(cameraControl.Get());
        ComPtr<IAMVideoProcAmp> videoControl;
        filter.As(&videoControl);
        PrintVideoProcAmp(videoControl.Get());
        moniker.Reset();
    }
    if (!found) {
        std::wcerr << L"DirectShow did not enumerate the Revopoint camera names.\n";
        return 1;
    }
    return 0;
}

int SetBrightness(const std::wstring& cameraPrefix, long requestedValue) {
    ComPtr<ICreateDevEnum> deviceEnumerator;
    HRESULT hr = CoCreateInstance(CLSID_SystemDeviceEnum, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&deviceEnumerator));
    if (FAILED(hr)) {
        std::wcerr << L"Could not create DirectShow device enumerator.\n";
        return 1;
    }
    ComPtr<IEnumMoniker> cameras;
    hr = deviceEnumerator->CreateClassEnumerator(CLSID_VideoInputDeviceCategory, &cameras, 0);
    if (hr != S_OK) {
        std::wcerr << L"No DirectShow video-capture devices were enumerated.\n";
        return 1;
    }

    ComPtr<IMoniker> moniker;
    while (cameras->Next(1, &moniker, nullptr) == S_OK) {
        const std::wstring name = FriendlyName(moniker.Get());
        if (name.rfind(cameraPrefix, 0) != 0) {
            moniker.Reset();
            continue;
        }
        ComPtr<IBaseFilter> filter;
        hr = moniker->BindToObject(nullptr, nullptr, IID_PPV_ARGS(&filter));
        if (FAILED(hr)) {
            std::wcerr << L"Could not bind camera filter for " << name << L".\n";
            return 1;
        }
        ComPtr<IAMVideoProcAmp> controls;
        hr = filter.As(&controls);
        if (FAILED(hr) || !controls) {
            std::wcerr << L"IAMVideoProcAmp is unavailable for " << name << L".\n";
            return 1;
        }

        long minimum = 0, maximum = 0, step = 0, defaultValue = 0, capabilities = 0;
        hr = controls->GetRange(VideoProcAmp_Brightness, &minimum, &maximum, &step,
                                &defaultValue, &capabilities);
        if (FAILED(hr)) {
            std::wcerr << L"Brightness range is unavailable for " << name << L".\n";
            return 1;
        }
        if ((capabilities & VideoProcAmp_Flags_Manual) == 0 || requestedValue < minimum ||
            requestedValue > maximum || step <= 0 || (requestedValue - minimum) % step != 0) {
            std::wcerr << L"Brightness value is unsupported by " << name << L"; range="
                       << minimum << L".." << maximum << L" step=" << step << L".\n";
            return 2;
        }

        long before = 0, beforeFlags = 0;
        controls->Get(VideoProcAmp_Brightness, &before, &beforeFlags);
        hr = controls->Set(VideoProcAmp_Brightness, requestedValue, VideoProcAmp_Flags_Manual);
        if (FAILED(hr)) {
            std::wcerr << L"Could not set brightness for " << name << L" (HRESULT 0x"
                       << std::hex << hr << std::dec << L").\n";
            return 1;
        }
        long after = 0, afterFlags = 0;
        hr = controls->Get(VideoProcAmp_Brightness, &after, &afterFlags);
        if (FAILED(hr) || after != requestedValue ||
            (afterFlags & VideoProcAmp_Flags_Manual) == 0) {
            std::wcerr << L"Brightness write did not verify on " << name << L".\n";
            return 1;
        }
        std::wcout << L"Camera: " << name << L"\nBrightness: " << before << L" -> "
                   << after << L" (manual)\n";
        return 0;
    }
    std::wcerr << L"No camera name starts with " << cameraPrefix << L".\n";
    return 1;
}

int Run(int argc, wchar_t** argv) {
    const HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        std::wcerr << L"CoInitializeEx failed: 0x" << std::hex << hr << L"\n";
        return 1;
    }
    int result = 0;
    if (argc == 1) {
        result = RunDevices();
    } else if (argc == 4 && std::wstring(argv[1]) == L"--set-brightness") {
        std::size_t parsed = 0;
        try {
            const long value = std::stol(argv[3], &parsed);
            if (parsed != std::wstring(argv[3]).size()) {
                throw std::invalid_argument("trailing characters");
            }
            result = SetBrightness(argv[2], value);
        } catch (const std::exception&) {
            std::wcerr << L"Brightness must be an integer.\n";
            result = 2;
        }
    } else {
        std::wcerr << L"Usage: CameraControls.exe [--set-brightness <camera-prefix> <value>]\n"
                   << L"The default invocation only reads controls.\n";
        result = 2;
    }
    CoUninitialize();
    return result;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    return Run(argc, argv);
}
