#pragma once

#include <cstddef>

#if defined(_WIN32)
#define CADYX_API extern "C" __declspec(dllexport)
#else
#define CADYX_API extern "C" __attribute__((visibility("default")))
#endif

#define CADYX_KERNEL_ABI_VERSION 1

#define CADYX_ABI_EXPORTS(X) \
  X(cadyx_kernel_version) \
  X(cadyx_kernel_abi_manifest) \
  X(cadyx_free) \
  X(cadyx_graph) \
  X(cadyx_brep_export) \
  X(cadyx_step_import) \
  X(cadyx_section_profile) \
  X(cadyx_step_export) \
  X(cadyx_stl_export) \
  X(cadyx_modify_imported_brep) \
  X(cadyx_box) \
  X(cadyx_profile) \
  X(cadyx_periodic_spline_samples) \
  X(cadyx_fuse_profiles) \
  X(cadyx_brep_distance) \
  X(cadyx_brep_preservation) \
  X(cadyx_brep_global_thickness) \
  X(cadyx_brep_thickness) \
  X(cadyx_advanced_brep) \
  X(cadyx_agent_feature_brep) \
  X(cadyx_brep_metrics) \
  X(cadyx_agent_topology) \
  X(cadyx_face_topology)

struct CadyxOperationNode {
  int type;
  int input_a;
  int input_b;
  int operation;
  int plane;
  int centered;
  double amount;
  std::size_t coordinate_offset;
  std::size_t coordinate_count;
  std::size_t edge_offset;
  std::size_t edge_count;
};

using CadyxProgressCallback = void (*)(void*, const char*, double);

CADYX_API const char* cadyx_kernel_version() noexcept;
CADYX_API const char* cadyx_kernel_abi_manifest() noexcept;
CADYX_API void cadyx_free(char* value) noexcept;
CADYX_API char* cadyx_graph(
    const CadyxOperationNode* nodes, std::size_t node_count,
    const double* coordinates, std::size_t coordinate_count,
    const int* edge_indices, std::size_t edge_count,
    const unsigned char* brep_data, std::size_t brep_size,
    int mesh, int topology, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_export(
    const CadyxOperationNode* nodes, std::size_t node_count,
    const double* coordinates, std::size_t coordinate_count,
    const int* edge_indices, std::size_t edge_count,
    const unsigned char* brep_data, std::size_t brep_size,
    std::size_t* output_size, char** metrics_json, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_step_import(
    const unsigned char* data, std::size_t size, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_section_profile(
    const unsigned char* data, std::size_t size, int normal_axis, double offset,
    std::size_t max_points, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_step_export(
    const unsigned char* data, std::size_t size, std::size_t* output_size,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_stl_export(
    const unsigned char* data, std::size_t size, double linear_deflection,
    double angular_deflection, std::size_t* output_size, char** metrics_json,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_modify_imported_brep(
    const unsigned char* data, std::size_t size, int operation,
    const double* parameters, std::size_t parameter_count,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_box(
    double width, double height, double depth, int mesh,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_profile(
    const double* coordinates, std::size_t point_count, int operation, int plane,
    double amount, int centered, int mesh, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_periodic_spline_samples(
    const double* coordinates, std::size_t point_count, std::size_t samples_per_span,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_fuse_profiles(
    const double* base_coordinates, std::size_t base_point_count,
    int base_operation, int base_plane, double base_amount, int base_centered,
    const double* tool_coordinates, std::size_t tool_point_count,
    int tool_operation, int tool_plane, double tool_amount, int tool_centered,
    int mesh, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_distance(
    const unsigned char* first_data, std::size_t first_size,
    const unsigned char* second_data, std::size_t second_size,
    CadyxProgressCallback progress_callback, void* progress_user_data,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_preservation(
    const unsigned char* reference_data, std::size_t reference_size,
    const unsigned char* candidate_data, std::size_t candidate_size,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_global_thickness(
    const unsigned char* data, std::size_t size,
    CadyxProgressCallback progress_callback, void* progress_user_data,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_thickness(
    const unsigned char* data, std::size_t size, double minimum_thickness,
    double tolerance, CadyxProgressCallback progress_callback,
    void* progress_user_data, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_advanced_brep(
    const unsigned char* data, std::size_t size, int operation,
    const double* values, std::size_t value_count, const int* indices,
    std::size_t index_count, std::size_t* output_size, char* generated_faces_json,
    std::size_t generated_faces_capacity, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_agent_feature_brep(
    const unsigned char* data, std::size_t size, int operation,
    const double* values, std::size_t value_count, std::size_t* output_size,
    char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_brep_metrics(
    const unsigned char* data, std::size_t size, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_agent_topology(
    const unsigned char* data, std::size_t size, char* error, std::size_t capacity) noexcept;
CADYX_API char* cadyx_face_topology(
    const unsigned char* data, std::size_t size, char* error, std::size_t capacity) noexcept;
