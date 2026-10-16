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

int Run() {
    const HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        std::wcerr << L"CoInitializeEx failed: 0x" << std::hex << hr << L"\n";
        return 1;
    }
    const int result = RunDevices();
    CoUninitialize();
    return result;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc != 1) {
        std::wcerr << L"CameraControls.exe only reads standard DirectShow camera controls.\n";
        return 2;
    }
    return Run();
}
