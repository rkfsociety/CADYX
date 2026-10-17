#include "kernel_internal.hpp"

#include <sstream>
#include <string>

using namespace cadyx::native;

namespace {
std::string BuildAbiManifest() {
#define CADYX_ABI_NAME(name) #name,
  static constexpr const char* exports[] = {CADYX_ABI_EXPORTS(CADYX_ABI_NAME)};
#undef CADYX_ABI_NAME
  std::ostringstream json;
  json << "{\"abi_version\":" << CADYX_KERNEL_ABI_VERSION << ",\"exports\":[";
  for (std::size_t index = 0; index < sizeof(exports) / sizeof(exports[0]); ++index)
    json << (index ? "," : "") << "\"" << exports[index] << "\"";
  json << "]}";
  return json.str();
}
}  // namespace

CADYX_API const char* cadyx_kernel_version() noexcept { return OCC_VERSION_COMPLETE; }
CADYX_API const char* cadyx_kernel_abi_manifest() noexcept {
  static const std::string manifest = BuildAbiManifest();
  return manifest.c_str();
}
CADYX_API void cadyx_free(char* value) noexcept { delete[] value; }

CADYX_API char* cadyx_graph(const OperationNode* nodes, size_t node_count,
                      const double* coordinates, size_t coordinate_count,
                      const int* edge_indices, size_t edge_count,
                      const unsigned char* brep_data, size_t brep_size,
                      int mesh, int topology, char* error, size_t capacity) noexcept {
  try {
    return CopyResult(BuildGraphJson(nodes, node_count, coordinates, coordinate_count,
                                     edge_indices, edge_count, brep_data, brep_size, mesh, topology));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT graph operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_brep_export(const OperationNode* nodes, size_t node_count,
                            const double* coordinates, size_t coordinate_count,
                            const int* edge_indices, size_t edge_count,
                            const unsigned char* brep_data, size_t brep_size,
                            size_t* output_size, char** metrics_json,
                            char* error, size_t capacity) noexcept {
  if (output_size) *output_size = 0;
  if (metrics_json) *metrics_json = nullptr;
  try {
    if (!output_size || !metrics_json)
      throw std::runtime_error("B-rep export needs output-size and metrics pointers.");
    const GeometryExportResult output = SerializeGraphForExport(
        nodes, node_count, coordinates, coordinate_count, edge_indices, edge_count,
        brep_data, brep_size);
    std::unique_ptr<char[]> result(CopyBytes(output.brep));
    std::unique_ptr<char[]> metrics(CopyResult(output.metrics));
    *output_size = output.brep.size();
    *metrics_json = metrics.release();
    return result.release();
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT B-rep export failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown OCCT B-rep export failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_step_import(const unsigned char* data, size_t size,
                            char* error, size_t capacity) noexcept {
  try {
    return CopyResult(ImportStepJson(data, size));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT STEP import failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown OCCT STEP import failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_section_profile(const unsigned char* data, size_t size, int normal_axis,
                                double offset, size_t max_points,
                                char* error, size_t capacity) noexcept {
  try {
    return CopyResult(SectionProfileJson(data, size, normal_axis, offset, max_points));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT could not extract the B-rep section.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native section extraction failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_step_export(const unsigned char* data, size_t size, size_t* output_size,
                            char* error, size_t capacity) noexcept {
  if (output_size) *output_size = 0;
  try {
    if (!output_size) throw std::runtime_error("STEP export needs an output-size pointer.");
    const std::string output = ExportStepBytes(data, size);
    *output_size = output.size();
    return CopyBytes(output);
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT STEP export failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown OCCT STEP export failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_stl_export(const unsigned char* data, size_t size,
                           double linear_deflection, double angular_deflection,
                           size_t* output_size, char** metrics_json,
                           char* error, size_t capacity) noexcept {
  if (output_size) *output_size = 0;
  if (metrics_json) *metrics_json = nullptr;
  try {
    if (!output_size || !metrics_json)
      throw std::runtime_error("STL export needs output-size and metrics pointers.");
    const StlExportResult output = ExportStl(data, size, linear_deflection, angular_deflection);
    std::unique_ptr<char[]> result(CopyBytes(output.data));
    std::unique_ptr<char[]> metrics(CopyResult(output.metrics));
    *output_size = output.data.size();
    *metrics_json = metrics.release();
    return result.release();
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT STL export failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown OCCT STL export failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_modify_imported_brep(const unsigned char* data, size_t size, int operation,
                                    const double* parameters, size_t parameter_count,
                                    char* error, size_t capacity) noexcept {
  try {
    return CopyResult(ModifyImportedBrepJson(data, size, operation, parameters, parameter_count));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT imported B-rep modification failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native B-rep modification failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_box(double width, double height, double depth, int mesh,
                    char* error, size_t capacity) noexcept {
  try {
    return CopyResult(BoxJson(width, height, depth, mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_profile(const double* coordinates, size_t point_count, int operation,
                       int plane, double amount, int centered, int mesh,
                       char* error, size_t capacity) noexcept {
  try {
    return CopyResult(ShapeJson(MakeProfileShape(coordinates, point_count, operation, plane,
                                                 amount, centered != 0), mesh, 0, false, nullptr));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_periodic_spline_samples(const double* coordinates, size_t point_count,
                                       size_t samples_per_span,
                                       char* error, size_t capacity) noexcept {
  try {
    return CopyResult(PeriodicSplineSamplesJson(coordinates, point_count, samples_per_span));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT periodic spline sampling failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown periodic spline sampling failure.");
  }
  return nullptr;
}

CADYX_API char* cadyx_fuse_profiles(
    const double* base_coordinates, size_t base_point_count, int base_operation,
    int base_plane, double base_amount, int base_centered,
    const double* tool_coordinates, size_t tool_point_count, int tool_operation,
    int tool_plane, double tool_amount, int tool_centered, int mesh,
    char* error, size_t capacity) noexcept {
  try {
    return CopyResult(FuseProfilesJson(
        base_coordinates, base_point_count, base_operation, base_plane, base_amount, base_centered,
        tool_coordinates, tool_point_count, tool_operation, tool_plane, tool_amount, tool_centered,
        mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT fuse failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}


CADYX_API char* cadyx_brep_distance(
    const unsigned char* first_data, size_t first_size,
    const unsigned char* second_data, size_t second_size,
    NativeProgressCallback progress_callback, void* progress_user_data,
    char* error, size_t capacity) noexcept {
  return BrepDistanceImpl(first_data, first_size, second_data, second_size,
                          progress_callback, progress_user_data, error, capacity);
}

CADYX_API char* cadyx_brep_preservation(
    const unsigned char* reference_data, size_t reference_size,
    const unsigned char* candidate_data, size_t candidate_size,
    char* error, size_t capacity) noexcept {
  return BrepPreservationImpl(reference_data, reference_size, candidate_data, candidate_size,
                              error, capacity);
}

CADYX_API char* cadyx_brep_global_thickness(
    const unsigned char* data, size_t size, NativeProgressCallback progress_callback,
    void* progress_user_data, char* error, size_t capacity) noexcept {
  return BrepGlobalThicknessImpl(data, size, progress_callback, progress_user_data, error, capacity);
}

CADYX_API char* cadyx_brep_thickness(
    const unsigned char* data, size_t size, double minimum_thickness, double tolerance,
    NativeProgressCallback progress_callback, void* progress_user_data,
    char* error, size_t capacity) noexcept {
  return BrepThicknessImpl(data, size, minimum_thickness, tolerance,
                           progress_callback, progress_user_data, error, capacity);
}

CADYX_API char* cadyx_advanced_brep(
    const unsigned char* data, size_t size, int operation,
    const double* values, size_t value_count, const int* indices, size_t index_count,
    size_t* output_size, char* generated_faces_json, size_t generated_faces_capacity,
    char* error, size_t capacity) noexcept {
  return AdvancedBrepImpl(data, size, operation, values, value_count, indices, index_count,
                          output_size, generated_faces_json, generated_faces_capacity, error, capacity);
}

CADYX_API char* cadyx_agent_feature_brep(
    const unsigned char* data, size_t size, int operation,
    const double* values, size_t value_count, size_t* output_size,
    char* error, size_t capacity) noexcept {
  return AgentFeatureBrepImpl(data, size, operation, values, value_count,
                              output_size, error, capacity);
}

CADYX_API char* cadyx_brep_metrics(
    const unsigned char* data, size_t size, char* error, size_t capacity) noexcept {
  return BrepMetricsImpl(data, size, error, capacity);
}

CADYX_API char* cadyx_agent_topology(
    const unsigned char* data, size_t size, char* error, size_t capacity) noexcept {
  return AgentTopologyImpl(data, size, error, capacity);
}

CADYX_API char* cadyx_face_topology(
    const unsigned char* data, size_t size, char* error, size_t capacity) noexcept {
  return FaceTopologyImpl(data, size, error, capacity);
}
