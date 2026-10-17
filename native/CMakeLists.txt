cmake_minimum_required(VERSION 3.24)
project(cadyx_kernel LANGUAGES CXX)
if(POLICY CMP0144)
  cmake_policy(SET CMP0144 NEW)
endif()
if(NOT OCCT_ROOT)
  message(FATAL_ERROR "Set OCCT_ROOT to the OCCT SDK directory")
endif()
if(NOT OPEN3D_ROOT)
  message(FATAL_ERROR "Set OPEN3D_ROOT to the Open3D SDK directory")
endif()

add_library(cadyx_kernel SHARED
  kernel.cpp
  kernel_common.cpp
  kernel_primitives.cpp
  kernel_topology.cpp
  kernel_delivery.cpp
  kernel_validation.cpp
  advanced.cpp
  advanced_topology.cpp
  agent_features.cpp
)
target_compile_features(cadyx_kernel PRIVATE cxx_std_17)
target_include_directories(cadyx_kernel PRIVATE "${OCCT_ROOT}/Library/include/opencascade")
target_link_directories(cadyx_kernel PRIVATE "${OCCT_ROOT}/Library/lib")
target_link_libraries(cadyx_kernel PRIVATE TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep
  TKGeomAlgo TKTopAlgo TKPrim TKBO TKBool TKMesh TKOffset TKFillet TKShHealing TKDESTEP TKDESTL TKXSBase)

find_package(Open3D CONFIG REQUIRED PATHS "${OPEN3D_ROOT}/CMake" NO_DEFAULT_PATH)
add_library(cadyx_scan SHARED scan.cpp)
target_compile_features(cadyx_scan PRIVATE cxx_std_17)
target_link_libraries(cadyx_scan PRIVATE Open3D::Open3D)
