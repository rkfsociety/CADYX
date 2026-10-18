#pragma once

#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Section.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_GTransform.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepFill.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRepOffsetAPI_MakePipe.hxx>
#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepClass3d_SolidClassifier.hxx>
#include <IntCurvesFace_ShapeIntersector.hxx>
#include <Precision.hxx>
#include <Message_ProgressIndicator.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <IMeshTools_Parameters.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools_ReShape.hxx>
#include <Bnd_Box.hxx>
#include <BRepBndLib.hxx>
#include <GeomAPI_Interpolate.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Pln.hxx>
#include <gp_Vec.hxx>
#include <gp_GTrsf.hxx>
#include <gp_Trsf.hxx>
#include <GProp_GProps.hxx>
#include <GeomAPI_PointsToBSpline.hxx>
#include <Geom_BSplineCurve.hxx>
#include <GeomAbs_CurveType.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <NCollection_Array1.hxx>
#include <NCollection_HArray1.hxx>
#include <TopExp_Explorer.hxx>
#include <TopExp.hxx>
#include <NCollection_IndexedDataMap.hxx>
#include <NCollection_List.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <NCollection_IndexedMap.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Solid.hxx>
#include <TopLoc_Location.hxx>
#include <Standard_Version.hxx>
#include <Standard_Failure.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <STEPControl_StepModelType.hxx>
#include <DESTEP_Parameters.hxx>
#include <StlAPI_Writer.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <ShapeFix_Shape.hxx>
#include <ShapeFix_Wire.hxx>
#include <ShapeFix_Solid.hxx>
#include <ShapeFix_Shell.hxx>
#include <algorithm>
#include <array>
#include <cstddef>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <iostream>
#include <locale>
#include <memory>
#include <string>
#include <stdexcept>
#include <cstdint>
#include <limits>
#include <unordered_set>
#include <vector>

#include "kernel_abi.hpp"

namespace cadyx::native {
using OperationNode = CadyxOperationNode;
using NativeProgressCallback = CadyxProgressCallback;

inline constexpr double kFullTurn = 6.28318530717958647692;
inline constexpr std::size_t kStlDeviationSampleLimit = 64;
inline constexpr std::size_t kMaxDeliveryBytes = 64 * 1024 * 1024;

class NativeProgressIndicator final : public Message_ProgressIndicator {
  DEFINE_STANDARD_RTTI_INLINE(NativeProgressIndicator, Message_ProgressIndicator)
public:
  NativeProgressIndicator(NativeProgressCallback callback, void* user_data)
      : callback_(callback), user_data_(user_data) {}

  void SetPhase(const char* phase) { phase_ = phase; }
  void Report(double progress, bool force = false) {
    if (callback_ && (force || progress - last_reported_ >= 0.01 || progress >= 1.0)) {
      callback_(user_data_, phase_.c_str(), progress);
      last_reported_ = progress;
    }
  }

protected:
  void Show(const Message_ProgressScope&, const bool) override { Report(GetPosition()); }

private:
  NativeProgressCallback callback_;
  void* user_data_;
  std::string phase_;
  double last_reported_ = -1.0;
};

struct StlExportResult { std::string data; std::string metrics; };
struct GeometryExportResult { std::string brep; std::string metrics; };

void CopyError(char* error, size_t capacity, const char* message);
char* CopyResult(const std::string& value);
char* CopyBytes(const std::string& value);
const char* CurveName(GeomAbs_CurveType type);
gp_Vec FaceNormalNearPoint(const TopoDS_Face& face, const gp_Pnt& point);
std::string ShapeJson(const TopoDS_Shape& shape, int mesh, int topology, bool allow_invalid_import, const std::vector<int>* generated_face_indices);
std::string Base64Encode(const std::string& input);
TopoDS_Shape ReadImportedBrep(const unsigned char* data, size_t size);
double ShapeVolume(const TopoDS_Shape& shape);
TopoDS_Shape RepairImportedBrep(const TopoDS_Shape& source);
TopoDS_Shape ResizeImportedThreadX(const TopoDS_Shape& source, const double* parameters);
TopoDS_Shape TransformImportedBrep(const TopoDS_Shape& source, const double* parameters);
std::string ModifiedBrepJson(const TopoDS_Shape& shape, bool repaired);
std::string ModifyImportedBrepJson(const unsigned char* data, std::size_t size, int operation,
                                  const double* parameters, std::size_t parameter_count);
std::string ImportStepJson(const unsigned char* data, size_t size);
std::string SectionProfileJson(const unsigned char* data, size_t size, int normal_axis, double offset, size_t max_points);
std::string ExportStepBytes(const unsigned char* data, size_t size);
StlExportResult ExportStl(const unsigned char* data, size_t size, double linear_deflection, double angular_deflection);
TopoDS_Shape MakeProfileShape(const double* coordinates, size_t point_count, int operation, int plane, double amount, bool centered);
std::string BoxJson(double width, double height, double depth, int mesh);
std::string FuseProfilesJson(
    const double* base_coordinates, size_t base_point_count, int base_operation,
    int base_plane, double base_amount, int base_centered,
    const double* tool_coordinates, size_t tool_point_count, int tool_operation,
    int tool_plane, double tool_amount, int tool_centered, int mesh);
Handle(NCollection_HArray1<double>) MakePeriodicSplineParameters(const double* coordinates, size_t point_count);
Handle(Geom_BSplineCurve) MakePeriodicSplineCurve(const double* coordinates, size_t point_count, int plane, Handle(NCollection_HArray1<double>)& parameters);
std::string PeriodicSplineSamplesJson(const double* coordinates, size_t point_count, size_t samples_per_span);
TopoDS_Shape MakeClosedSplineProfileShape(const double* coordinates, size_t point_count, int operation, int plane, double amount, bool centered);
TopoDS_Shape MakePeriodicSplineProfileShape(const double* coordinates, size_t point_count, int operation, int plane, double amount, bool centered);
TopoDS_Shape MakePipeShape(const double* coordinates, size_t point_count, int plane, double radius);
TopoDS_Shape MakeLoftShape(const double* coordinates, const int* section_sizes, size_t section_count, size_t coordinate_count, int section_axis);
TopoDS_Edge MakeRuledRail(const double* coordinates, size_t point_count);
TopoDS_Shape MakeRuledSolid(const double* coordinates, size_t coordinate_count, const int* patch_sizes, size_t patch_count, double tolerance);
TopoDS_Shape EnsureOneSolid(const TopoDS_Shape& shape, const char* operation);
TopoDS_Solid ExtractSingleSolid(const TopoDS_Shape& shape);
TopoDS_Shape MakeFillet(const TopoDS_Shape& source, const int* edge_indices, size_t edge_count, double radius, std::vector<int>* generated_face_indices);
TopoDS_Shape MakeFuse(const TopoDS_Shape& base, const TopoDS_Shape& tool);
TopoDS_Shape MakeFuseParts(const TopoDS_Shape& base, const TopoDS_Shape& tool);
TopoDS_Shape MakeFusePartsBatch(const std::vector<TopoDS_Shape>& shapes, const int* shape_indices, size_t shape_count);
TopoDS_Shape MakeCut(const TopoDS_Shape& base, const TopoDS_Shape& tool);
TopoDS_Shape MakeMetricThread(double major_diameter, double pitch, double length, int handedness);
TopoDS_Shape BuildGraphShape(const OperationNode* nodes, size_t node_count, const double* coordinates, size_t coordinate_count, const int* edge_indices, size_t edge_count, const unsigned char* brep_data, size_t brep_size, bool& allow_invalid_import, std::vector<int>* generated_face_indices);
std::string BuildGraphJson(const OperationNode* nodes, size_t node_count, const double* coordinates, size_t coordinate_count, const int* edge_indices, size_t edge_count, const unsigned char* brep_data, size_t brep_size, int mesh, int topology);
GeometryExportResult SerializeGraphForExport(const OperationNode* nodes, size_t node_count, const double* coordinates, size_t coordinate_count, const int* edge_indices, size_t edge_count, const unsigned char* brep_data, size_t brep_size);
char* BrepDistanceImpl(const unsigned char* first_data, std::size_t first_size,
                       const unsigned char* second_data, std::size_t second_size,
                       NativeProgressCallback progress_callback, void* progress_user_data,
                       char* error, std::size_t capacity) noexcept;
char* BrepPreservationImpl(const unsigned char* reference_data, std::size_t reference_size,
                           const unsigned char* candidate_data, std::size_t candidate_size,
                           char* error, std::size_t capacity) noexcept;
char* BrepGlobalThicknessImpl(const unsigned char* data, std::size_t size,
                              NativeProgressCallback progress_callback, void* progress_user_data,
                              char* error, std::size_t capacity) noexcept;
char* BrepThicknessImpl(const unsigned char* data, std::size_t size,
                        double minimum_thickness, double tolerance,
                        NativeProgressCallback progress_callback, void* progress_user_data,
                        char* error, std::size_t capacity) noexcept;
char* AdvancedBrepImpl(const unsigned char* data, size_t size, int operation,
                       const double* values, size_t value_count, const int* indices,
                       size_t index_count, size_t* output_size, char* generated_faces_json,
                       size_t generated_faces_capacity, char* error, size_t capacity) noexcept;
char* AgentFeatureBrepImpl(const unsigned char* data, size_t size, int operation,
                           const double* values, size_t value_count, size_t* output_size,
                           char* error, size_t capacity) noexcept;
char* BrepMetricsImpl(const unsigned char* data, size_t size,
                      char* error, size_t capacity) noexcept;
char* AgentTopologyImpl(const unsigned char* data, size_t size,
                        char* error, size_t capacity) noexcept;
char* FaceTopologyImpl(const unsigned char* data, size_t size,
                       char* error, size_t capacity) noexcept;
}  // namespace cadyx::native
