// Small Python-ABI-independent boundary; OCCT objects never leave this library.
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
#include <NCollection_Array1.hxx>
#include <NCollection_HArray1.hxx>
#include <TopExp_Explorer.hxx>
#include <TopExp.hxx>
#include <NCollection_IndexedMap.hxx>
#include <NCollection_List.hxx>
#include <TopTools_ShapeMapHasher.hxx>
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

#define API extern "C" __declspec(dllexport)
API const char* cadyx_kernel_version() noexcept { return OCC_VERSION_COMPLETE; }
API void cadyx_free(char* value) noexcept { delete[] value; }

namespace {
using NativeProgressCallback = void (*)(void*, const char*, double);

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

constexpr double kFullTurn = 6.28318530717958647692;
constexpr size_t kStlDeviationSampleLimit = 64;
constexpr size_t kMaxDeliveryBytes = 64 * 1024 * 1024;

void CopyError(char* error, size_t capacity, const char* message) {
  if (!error || !capacity) return;
  const size_t count = std::min(std::strlen(message), capacity - 1);
  std::memcpy(error, message, count);
  error[count] = '\0';
}

char* CopyResult(const std::string& value) {
  char* result = new char[value.size() + 1];
  std::memcpy(result, value.c_str(), value.size() + 1);
  return result;
}

char* CopyBytes(const std::string& value) {
  if (value.empty()) throw std::runtime_error("OCCT returned an empty export.");
  char* result = new char[value.size()];
  std::memcpy(result, value.data(), value.size());
  return result;
}

const char* CurveName(GeomAbs_CurveType type) {
  switch (type) {
    case GeomAbs_Line: return "line";
    case GeomAbs_Circle: return "circle";
    case GeomAbs_Ellipse: return "ellipse";
    case GeomAbs_Hyperbola: return "hyperbola";
    case GeomAbs_Parabola: return "parabola";
    case GeomAbs_BezierCurve: return "bezier";
    case GeomAbs_BSplineCurve: return "bspline";
    case GeomAbs_OffsetCurve: return "offset";
    case GeomAbs_OtherCurve: return "other";
  }
  return "other";
}

std::string ShapeJson(const TopoDS_Shape& shape, int mesh, int topology = 0,
                      bool allow_invalid_import = false) {
  if (shape.IsNull()) throw std::runtime_error("OCCT returned a null shape.");
  const bool brep_valid = BRepCheck_Analyzer(shape).IsValid();
  if (!brep_valid && !allow_invalid_import)
    throw std::runtime_error("OCCT constructed an invalid solid.");
  size_t solid_count = 0;
  for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) ++solid_count;
  if (solid_count != 1) throw std::runtime_error("OCCT geometry must contain exactly one closed solid.");
  GProp_GProps properties;
  BRepGProp::VolumeProperties(shape, properties);
  const double volume = properties.Mass();
  if (!std::isfinite(volume) || volume <= 0)
    throw std::runtime_error("OCCT returned an invalid volume.");
  GProp_GProps surface_properties;
  BRepGProp::SurfaceProperties(shape, surface_properties);
  const double surface_area = surface_properties.Mass();
  if (!std::isfinite(surface_area) || surface_area <= 0)
    throw std::runtime_error("OCCT returned an invalid surface area.");
  Bnd_Box bounds;
  // Compare the actual geometric extents, independent of cached viewport
  // triangulations and B-rep tolerances that can inflate Add() bounds.
  BRepBndLib::AddOptimal(shape, bounds, false, false);
  double xmin, ymin, zmin, xmax, ymax, zmax;
  bounds.Get(xmin, ymin, zmin, xmax, ymax, zmax);
  const auto count_shapes = [&](TopAbs_ShapeEnum type) {
    NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped;
    TopExp::MapShapes(shape, type, mapped);
    return static_cast<size_t>(mapped.Extent());
  };
  const size_t face_count = count_shapes(TopAbs_FACE);
  const size_t edge_count = count_shapes(TopAbs_EDGE);
  const size_t vertex_count = count_shapes(TopAbs_VERTEX);

  std::ostringstream json;
  json.imbue(std::locale::classic());
  json << std::setprecision(17) << "{\"volume\":" << volume
       << ",\"brep_valid\":" << (brep_valid ? "true" : "false")
       << ",\"surface_area\":" << surface_area
       << ",\"bounds\":{\"min\":[" << xmin << "," << ymin << "," << zmin
       << "],\"max\":[" << xmax << "," << ymax << "," << zmax << "]}"
       << ",\"topology\":{\"solid_count\":" << solid_count
       << ",\"face_count\":" << face_count << ",\"edge_count\":" << edge_count
       << ",\"vertex_count\":" << vertex_count << "}";
  if (mesh) {
    // This mesh is only for the live viewport; the editable B-rep remains exact.
    // Keep the display approximation near one CSS pixel for a typical viewport;
    // the exact editable B-rep remains unchanged.
    BRepMesh_IncrementalMesh mesher(shape, 0.16, false, 0.5, false);
    if (!mesher.IsDone()) throw std::runtime_error("OCCT could not tessellate the solid.");
    std::vector<double> positions;
    std::vector<size_t> triangles;
    for (TopExp_Explorer explorer(shape, TopAbs_FACE); explorer.More(); explorer.Next()) {
      const TopoDS_Face face = TopoDS::Face(explorer.Current());
      TopLoc_Location location;
      const auto triangulation = BRep_Tool::Triangulation(face, location);
      if (triangulation.IsNull()) throw std::runtime_error("OCCT face has no triangulation.");
      const size_t offset = positions.size() / 3;
      for (int i = 1; i <= triangulation->NbNodes(); ++i) {
        const gp_Pnt point = triangulation->Node(i).Transformed(location.Transformation());
        for (const double value : {point.X(), point.Y(), point.Z()}) {
          if (!std::isfinite(value)) throw std::runtime_error("OCCT returned a non-finite vertex.");
          positions.push_back(value);
        }
      }
      for (int i = 1; i <= triangulation->NbTriangles(); ++i) {
        int a, b, c;
        triangulation->Triangle(i).Get(a, b, c);
        if (face.Orientation() == TopAbs_REVERSED) std::swap(b, c);
        triangles.insert(triangles.end(), {offset+a-1, offset+b-1, offset+c-1});
      }
    }
    if (triangles.empty()) throw std::runtime_error("OCCT returned an empty mesh.");
    json << ",\"positions\":[";
    for (size_t i = 0; i < positions.size(); ++i) json << (i ? "," : "") << positions[i];
    json << "],\"triangles\":[";
    for (size_t i = 0; i < triangles.size(); ++i) json << (i ? "," : "") << triangles[i];
    json << "]";
  }
  if (topology) {
    json << ",\"edges\":[";
    NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped_edges;
    TopExp::MapShapes(shape, TopAbs_EDGE, mapped_edges);
    for (int index = 0; index < mapped_edges.Extent(); ++index) {
      if (index) json << ",";
      const TopoDS_Edge edge = TopoDS::Edge(mapped_edges.FindKey(index + 1));
      GProp_GProps edge_properties;
      BRepGProp::LinearProperties(edge, edge_properties);
      const gp_Pnt center = edge_properties.CentreOfMass();
      BRepAdaptor_Curve curve(edge);
      json << "{\"index\":" << index << ",\"length\":" << edge_properties.Mass()
           << ",\"center\":[" << center.X() << "," << center.Y() << "," << center.Z()
           << "],\"curve\":\"" << CurveName(curve.GetType()) << "\"}";
    }
    json << "]";
  }
  json << "}";
  return json.str();
}

struct OperationNode {
  int type;
  int input_a;
  int input_b;
  int operation;
  int plane;
  int centered;
  double amount;
  size_t coordinate_offset;
  size_t coordinate_count;
  size_t edge_offset;
  size_t edge_count;
};

std::string Base64Encode(const std::string& input) {
  static constexpr char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string output;
  output.reserve((input.size() + 2) / 3 * 4);
  for (size_t offset = 0; offset < input.size(); offset += 3) {
    const uint32_t a = static_cast<unsigned char>(input[offset]);
    const uint32_t b = offset + 1 < input.size()
                           ? static_cast<unsigned char>(input[offset + 1]) : 0;
    const uint32_t c = offset + 2 < input.size()
                           ? static_cast<unsigned char>(input[offset + 2]) : 0;
    const uint32_t value = (a << 16) | (b << 8) | c;
    output.push_back(alphabet[(value >> 18) & 0x3f]);
    output.push_back(alphabet[(value >> 12) & 0x3f]);
    output.push_back(offset + 1 < input.size() ? alphabet[(value >> 6) & 0x3f] : '=');
    output.push_back(offset + 2 < input.size() ? alphabet[value & 0x3f] : '=');
  }
  return output;
}

TopoDS_Shape ReadImportedBrep(const unsigned char* data, size_t size) {
  if (!data || size == 0 || size > 64 * 1024 * 1024)
    throw std::runtime_error("Imported B-rep data is empty or exceeds the 64 MB limit.");
  const std::string serialized(reinterpret_cast<const char*>(data), size);
  std::istringstream input(serialized);
  TopoDS_Shape shape;
  BRep_Builder builder;
  BRepTools::Read(shape, input, builder);
  if (shape.IsNull()) throw std::runtime_error("OCCT could not read the imported B-rep.");
  return shape;
}

double ShapeVolume(const TopoDS_Shape& shape) {
  GProp_GProps properties;
  BRepGProp::VolumeProperties(shape, properties);
  return properties.Mass();
}

TopoDS_Shape EnsureOneSolid(const TopoDS_Shape& shape, const char* operation);
TopoDS_Solid ExtractSingleSolid(const TopoDS_Shape& shape);

TopoDS_Shape RepairImportedBrep(const TopoDS_Shape& source) {
  if (BRepCheck_Analyzer(source).IsValid()) return source;
  const double original_volume = ShapeVolume(source);
  Handle(BRepTools_ReShape) reshape = new BRepTools_ReShape();
  BRepCheck_Analyzer source_check(source);
  for (TopExp_Explorer explorer(source, TopAbs_FACE); explorer.More(); explorer.Next()) {
    const TopoDS_Face face = TopoDS::Face(explorer.Current());
    if (source_check.IsValid(face)) continue;
    BRepAdaptor_Surface surface(face, true);
    if (surface.GetType() != GeomAbs_Plane)
      throw std::runtime_error("Invalid imported B-rep has a non-planar face that cannot be repaired safely.");
    TopoDS_Wire wire;
    for (TopExp_Explorer wire_explorer(face, TopAbs_WIRE); wire_explorer.More(); wire_explorer.Next()) {
      wire = TopoDS::Wire(wire_explorer.Current());
      break;
    }
    if (wire.IsNull()) throw std::runtime_error("Invalid planar face has no wire to repair.");
    ShapeFix_Wire wire_fix(wire, face, 0.001);
    wire_fix.SetMaxTolerance(0.01);
    wire_fix.ModifyTopologyMode() = true;
    wire_fix.ModifyGeometryMode() = true;
    wire_fix.FixSelfIntersectionMode() = 1;
    wire_fix.FixSelfIntersectingEdgeMode() = 1;
    wire_fix.FixIntersectingEdgesMode() = 1;
    wire_fix.FixNonAdjacentIntersectingEdgesMode() = 1;
    if (!wire_fix.Perform()) throw std::runtime_error("Self-intersecting imported face wire could not be repaired.");
    BRepBuilderAPI_MakeFace face_builder(surface.Plane(), wire_fix.Wire(), true);
    if (!face_builder.IsDone()) throw std::runtime_error("OCCT could not rebuild a repaired planar face.");
    TopoDS_Face replacement = face_builder.Face();
    replacement.Orientation(face.Orientation());
    if (!BRepCheck_Analyzer(replacement).IsValid())
      throw std::runtime_error("Rebuilt imported planar face is still invalid.");
    reshape->Replace(face, replacement);
  }

  const TopoDS_Shape replaced_faces = reshape->Apply(source);
  BRepBuilderAPI_Sewing sewing(1e-5, true, true, true, false);
  for (TopExp_Explorer explorer(replaced_faces, TopAbs_FACE); explorer.More(); explorer.Next())
    sewing.Add(explorer.Current());
  sewing.Perform();
  const TopoDS_Shape sewed = sewing.SewedShape();
  size_t shell_count = 0;
  for (TopExp_Explorer explorer(sewed, TopAbs_SHELL); explorer.More(); explorer.Next()) ++shell_count;
  if (shell_count != 1 || sewing.NbFreeEdges() != 0 || sewing.NbMultipleEdges() != 0)
    throw std::runtime_error("Repaired imported faces did not form one closed manifold shell.");
  const TopoDS_Shell shell = TopoDS::Shell(sewed);
  if (!BRep_Tool::IsClosed(shell))
    throw std::runtime_error("Repaired imported shell is not closed.");
  BRepBuilderAPI_MakeSolid solid_builder(shell);
  if (!solid_builder.IsDone()) throw std::runtime_error("OCCT could not rebuild the imported solid.");
  ShapeFix_Solid solid_fix(solid_builder.Solid());
  solid_fix.SetPrecision(1e-7);
  solid_fix.SetMaxTolerance(1e-5);
  solid_fix.Perform();
  const TopoDS_Shape repaired = solid_fix.Solid();
  if (!BRepCheck_Analyzer(repaired).IsValid())
    throw std::runtime_error("Repaired imported B-rep remains invalid.");
  const double repaired_volume = ShapeVolume(repaired);
  if (!std::isfinite(original_volume) || original_volume <= 0
      || std::abs(repaired_volume - original_volume) > std::max(1e-6, original_volume * 1e-6))
    throw std::runtime_error("B-rep repair changed the source volume beyond its tolerance.");
  return repaired;
}

TopoDS_Shape ResizeImportedThreadX(const TopoDS_Shape& source, const double* parameters) {
  const double x_start = parameters[0];
  const double x_end = parameters[1];
  const double axis_y = parameters[2];
  const double axis_z = parameters[3];
  const double major_radius = parameters[4];
  const double diameter_delta = parameters[5];
  if (!std::isfinite(x_start) || !std::isfinite(x_end) || !std::isfinite(axis_y)
      || !std::isfinite(axis_z) || !std::isfinite(major_radius)
      || !std::isfinite(diameter_delta) || x_end <= x_start || major_radius <= 0
      || diameter_delta <= 0 || diameter_delta >= major_radius * 2)
    throw std::runtime_error("Imported thread resize parameters are outside the supported range.");

  Bnd_Box source_bounds;
  BRepBndLib::Add(source, source_bounds);
  double xmin, ymin, zmin, xmax, ymax, zmax;
  source_bounds.Get(xmin, ymin, zmin, xmax, ymax, zmax);
  if (x_start <= xmin + 1e-6 || x_end >= xmax - 1e-6)
    throw std::runtime_error("Thread resize interval must lie inside the imported body bounds.");
  const double radial_extent = std::max({std::abs(ymin - axis_y), std::abs(ymax - axis_y),
                                         std::abs(zmin - axis_z), std::abs(zmax - axis_z)});
  const double margin = radial_extent * 2.0 + 1.0;
  BRepPrimAPI_MakeBox clip_box(
      gp_Pnt(x_start, axis_y - margin, axis_z - margin),
      x_end - x_start, margin * 2.0, margin * 2.0);
  BRepAlgoAPI_Common slice_operation(source, clip_box.Shape());
  slice_operation.SetFuzzyValue(1e-5);
  slice_operation.SetRunParallel(true);
  slice_operation.Build();
  if (!slice_operation.IsDone()) throw std::runtime_error("OCCT could not isolate the imported thread region.");
  const TopoDS_Shape slice = EnsureOneSolid(slice_operation.Shape(), "Thread slice");
  if (!BRepCheck_Analyzer(slice).IsValid())
    throw std::runtime_error("Isolated imported thread region is invalid.");

  const double scale = (major_radius + diameter_delta / 2.0) / major_radius;
  gp_GTrsf radial_transform;
  radial_transform.SetValue(1, 1, 1.0);
  radial_transform.SetValue(2, 2, scale);
  radial_transform.SetValue(2, 4, axis_y * (1.0 - scale));
  radial_transform.SetValue(3, 3, scale);
  radial_transform.SetValue(3, 4, axis_z * (1.0 - scale));
  BRepBuilderAPI_GTransform resize_operation(slice, radial_transform, true);
  if (!resize_operation.IsDone()) throw std::runtime_error("OCCT could not scale the imported thread profile.");
  const TopoDS_Shape resized_slice = EnsureOneSolid(resize_operation.Shape(), "Resized thread slice");
  if (!BRepCheck_Analyzer(resized_slice).IsValid())
    throw std::runtime_error("Radially scaled imported thread region is invalid.");

  BRepAlgoAPI_Fuse stitch_operation(source, resized_slice);
  stitch_operation.SetFuzzyValue(1e-5);
  stitch_operation.SetRunParallel(true);
  stitch_operation.Build();
  if (!stitch_operation.IsDone())
    throw std::runtime_error("OCCT could not stitch the resized thread back into the imported body.");
  const TopoDS_Shape result = EnsureOneSolid(stitch_operation.Shape(), "Resized imported thread");
  if (!BRepCheck_Analyzer(result).IsValid())
    throw std::runtime_error("Stitched imported thread has invalid or self-intersecting B-rep topology.");
  if (ShapeVolume(result) <= ShapeVolume(source))
    throw std::runtime_error("Resized imported thread did not add material to the outside diameter.");
  return result;
}

TopoDS_Shape TransformImportedBrep(const TopoDS_Shape& source, const double* parameters) {
  const double axis_x = parameters[0];
  const double axis_y = parameters[1];
  const double axis_z = parameters[2];
  const double angle = parameters[3];
  const double translate_x = parameters[4];
  const double translate_y = parameters[5];
  const double translate_z = parameters[6];
  if (!std::isfinite(axis_x) || !std::isfinite(axis_y) || !std::isfinite(axis_z)
      || !std::isfinite(angle) || !std::isfinite(translate_x)
      || !std::isfinite(translate_y) || !std::isfinite(translate_z)
      || gp_Vec(axis_x, axis_y, axis_z).SquareMagnitude() <= 1e-24)
    throw std::runtime_error("Imported B-rep transform parameters are invalid.");
  gp_Trsf transform;
  transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(axis_x, axis_y, axis_z)), angle);
  transform.SetTranslationPart(gp_Vec(translate_x, translate_y, translate_z));
  BRepBuilderAPI_Transform operation(source, transform, true);
  if (!operation.IsDone()) throw std::runtime_error("OCCT could not align the imported B-rep.");
  const TopoDS_Shape result = operation.Shape();
  if (!BRepCheck_Analyzer(result).IsValid())
    throw std::runtime_error("Aligned imported B-rep is invalid.");
  return result;
}

std::string ModifiedBrepJson(const TopoDS_Shape& shape, bool repaired) {
  std::ostringstream brep;
  BRepTools::Write(shape, brep, false, false, TopTools_FormatVersion_CURRENT);
  if (!brep.good()) throw std::runtime_error("OCCT could not serialize the modified B-rep.");
  const std::string serialized = brep.str();
  if (serialized.empty() || serialized.size() > 64 * 1024 * 1024)
    throw std::runtime_error("Modified B-rep is empty or exceeds the 64 MB limit.");
  std::string json = ShapeJson(shape, 0);
  json.pop_back();
  json += std::string(",\"repaired\":") + (repaired ? "true" : "false")
      + ",\"brep_data\":\"" + Base64Encode(serialized) + "\"}";
  return json;
}

std::string ImportStepJson(const unsigned char* data, size_t size) {
  if (!data || size == 0 || size > 64 * 1024 * 1024)
    throw std::runtime_error("STEP source is empty or exceeds the 64 MB limit.");
  const std::string content(reinterpret_cast<const char*>(data), size);
  std::istringstream input(content);
  STEPControl_Reader reader;
  if (reader.ReadStream("CADYX STEP import", input) != IFSelect_RetDone)
    throw std::runtime_error("OCCT could not read the STEP source.");
  if (reader.NbRootsForTransfer() == 0 || reader.TransferRoots() == 0)
    throw std::runtime_error("STEP source contains no transferable solid.");
  TopoDS_Shape shape = reader.OneShape();
  size_t solid_count = 0;
  for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) ++solid_count;
  if (solid_count != 1)
    throw std::runtime_error("STEP import currently requires exactly one solid.");
  bool repaired = false;
  if (!shape.IsNull() && !BRepCheck_Analyzer(shape).IsValid()) {
    ShapeFix_Shape repair(shape);
    repair.SetPrecision(1e-7);
    repair.SetMaxTolerance(1e-5);
    repair.Perform();
    const TopoDS_Shape fixed = repair.Shape();
    if (!fixed.IsNull() && BRepCheck_Analyzer(fixed).IsValid()) {
      shape = fixed;
      repaired = true;
    }
  }
  std::ostringstream brep;
  BRepTools::Write(shape, brep, false, false, TopTools_FormatVersion_CURRENT);
  if (!brep.good()) throw std::runtime_error("OCCT could not serialize the imported B-rep.");
  const std::string serialized_brep = brep.str();
  if (serialized_brep.empty() || serialized_brep.size() > 64 * 1024 * 1024)
    throw std::runtime_error("Serialized imported B-rep is empty or exceeds the 64 MB limit.");
  std::string json = ShapeJson(shape, 0, 0, true);
  json.pop_back();
  json += std::string(",\"repaired\":") + (repaired ? "true" : "false")
      + ",\"brep_data\":\"" + Base64Encode(serialized_brep) + "\"}";
  return json;
}

std::string SectionProfileJson(const unsigned char* data, size_t size, int normal_axis,
                               double offset, size_t max_points) {
  const TopoDS_Shape source = ReadImportedBrep(data, size);
  if (!std::isfinite(offset) || normal_axis < 0 || normal_axis > 2 || max_points < 16)
    throw std::runtime_error("Section plane or profile sampling limit is invalid.");
  const gp_Dir normal = normal_axis == 0 ? gp_Dir(1, 0, 0)
      : normal_axis == 1 ? gp_Dir(0, 1, 0) : gp_Dir(0, 0, 1);
  const gp_Pnt origin = normal_axis == 0 ? gp_Pnt(offset, 0, 0)
      : normal_axis == 1 ? gp_Pnt(0, offset, 0) : gp_Pnt(0, 0, offset);
  BRepAlgoAPI_Section operation(source, gp_Pln(origin, normal), Standard_False);
  operation.ComputePCurveOn1(false);
  operation.Approximation(true);
  operation.Build();
  if (!operation.IsDone()) throw std::runtime_error("OCCT could not section the imported solid.");

  struct SectionEdge { TopoDS_Edge edge; gp_Pnt first; gp_Pnt last; double length; };
  std::vector<SectionEdge> edges;
  for (TopExp_Explorer explorer(operation.Shape(), TopAbs_EDGE); explorer.More(); explorer.Next()) {
    const TopoDS_Edge edge = TopoDS::Edge(explorer.Current());
    TopoDS_Vertex first_vertex, last_vertex;
    TopExp::Vertices(edge, first_vertex, last_vertex, true);
    if (first_vertex.IsNull() || last_vertex.IsNull())
      throw std::runtime_error("Section contains an edge without two finite endpoints.");
    GProp_GProps properties;
    BRepGProp::LinearProperties(edge, properties);
    const double length = properties.Mass();
    if (!std::isfinite(length) || length <= 1e-7)
      throw std::runtime_error("Section contains a degenerate edge.");
    edges.push_back({edge, BRep_Tool::Pnt(first_vertex), BRep_Tool::Pnt(last_vertex), length});
  }
  if (edges.empty() || edges.size() > max_points)
    throw std::runtime_error("Section must contain closed profiles within the point limit.");

  std::vector<bool> used(edges.size(), false);
  std::vector<std::vector<std::pair<size_t, bool>>> loops;
  for (size_t seed = 0; seed < edges.size(); ++seed) {
    if (used[seed]) continue;
    std::vector<std::pair<size_t, bool>> ordered;
    ordered.reserve(edges.size());
    const gp_Pnt start = edges[seed].first;
    gp_Pnt current = edges[seed].last;
    used[seed] = true;
    ordered.emplace_back(seed, false);
    while (current.Distance(start) > 1e-5) {
      size_t next = edges.size();
      bool reverse = false;
      for (size_t index = 0; index < edges.size(); ++index) {
        if (used[index]) continue;
        if (current.Distance(edges[index].first) <= 1e-5) { next = index; break; }
        if (current.Distance(edges[index].last) <= 1e-5) { next = index; reverse = true; break; }
      }
      if (next == edges.size())
        throw std::runtime_error("Section boundary is open or contains a branched contour.");
      used[next] = true;
      ordered.emplace_back(next, reverse);
      current = reverse ? edges[next].first : edges[next].last;
      if (ordered.size() > edges.size()) throw std::runtime_error("Section boundary is branched.");
    }
    loops.push_back(std::move(ordered));
  }
  double total_length = 0;
  for (const auto& edge : edges) total_length += edge.length;
  if (!std::isfinite(total_length) || total_length <= 0)
    throw std::runtime_error("Section boundary has an invalid total length.");
  if (edges.size() > max_points)
    throw std::runtime_error("Section has more boundary edges than the requested sample limit.");
  std::vector<std::vector<gp_Pnt>> profiles;
  profiles.reserve(loops.size());
  const size_t remaining_samples = max_points - edges.size();
  size_t additional_samples_used = 0;
  for (const auto& ordered : loops) {
    std::vector<gp_Pnt> points;
    for (const auto& item : ordered) {
      const auto& edge = edges[item.first];
      BRepAdaptor_Curve curve(edge.edge);
      const double first = curve.FirstParameter();
      const double last = curve.LastParameter();
      if (!std::isfinite(first) || !std::isfinite(last) || !(last > first))
        throw std::runtime_error("Section edge has unsupported parameter bounds.");
      const double proportional_samples =
          static_cast<double>(remaining_samples) * edge.length / total_length;
      const size_t remaining_budget = remaining_samples - additional_samples_used;
      const size_t additional_segments = proportional_samples >= static_cast<double>(remaining_budget)
          ? remaining_budget : static_cast<size_t>(std::floor(proportional_samples));
      additional_samples_used += additional_segments;
      const size_t segments = 1 + additional_segments;
      for (size_t sample = 0; sample < segments; ++sample) {
        const double ratio = static_cast<double>(sample) / segments;
        const double parameter = item.second ? last - (last - first) * ratio
                                             : first + (last - first) * ratio;
        points.push_back(curve.Value(parameter));
      }
    }
    if (points.size() < 3) continue;
    double twice_area = 0;
    for (size_t index = 0; index < points.size(); ++index) {
      const gp_Pnt& point = points[index];
      const gp_Pnt& next = points[(index + 1) % points.size()];
      const double x = normal_axis == 0 ? point.Y() : point.X();
      const double y = normal_axis == 2 ? point.Y() : point.Z();
      const double next_x = normal_axis == 0 ? next.Y() : next.X();
      const double next_y = normal_axis == 2 ? next.Y() : next.Z();
      twice_area += x * next_y - next_x * y;
    }
    if (std::isfinite(twice_area) && std::abs(twice_area) > 1e-12)
      profiles.push_back(std::move(points));
  }
  if (profiles.empty())
    throw std::runtime_error("Section contains no closed contour with non-zero area.");

  std::ostringstream json;
  json.imbue(std::locale::classic());
  json << std::setprecision(17) << "{\"closed\":true,\"profiles\":[";
  for (size_t profile_index = 0; profile_index < profiles.size(); ++profile_index) {
    if (profile_index) json << ',';
    json << '[';
    const auto& points = profiles[profile_index];
    for (size_t index = 0; index < points.size(); ++index) {
      const auto& point = points[index];
      const double x = normal_axis == 0 ? point.Y() : point.X();
      const double y = normal_axis == 2 ? point.Y() : point.Z();
      if (!std::isfinite(x) || !std::isfinite(y))
        throw std::runtime_error("OCCT returned a non-finite section point.");
      if (index) json << ',';
      json << "{\"x\":" << x << ",\"y\":" << y << '}';
    }
    json << ']';
  }
  json << "]}";
  return json.str();
}

std::string ExportStepBytes(const unsigned char* data, size_t size) {
  const TopoDS_Solid solid = ExtractSingleSolid(ReadImportedBrep(data, size));
  ShapeJson(solid, 0);
  STEPControl_Writer writer;
  DESTEP_Parameters transfer_parameters;
  transfer_parameters.InitFromStatic();
  transfer_parameters.WritePrecisionMode = DESTEP_Parameters::WriteMode_PrecisionMode_Greatest;
  if (writer.Transfer(solid, STEPControl_ManifoldSolidBrep, transfer_parameters) != IFSelect_RetDone)
    throw std::runtime_error("OCCT could not transfer the B-rep to STEP.");
  std::ostringstream output;
  if (writer.WriteStream(output) != IFSelect_RetDone || !output.good())
    throw std::runtime_error("OCCT could not write the STEP export.");
  const std::string step = output.str();
  if (step.empty() || step.size() > kMaxDeliveryBytes)
    throw std::runtime_error("STEP output is empty or exceeds the 64 MB delivery limit.");
  return step;
}

struct StlExportResult {
  std::string data;
  std::string metrics;
};

StlExportResult ExportStl(const unsigned char* data, size_t size,
                          double linear_deflection, double angular_deflection) {
  if (!std::isfinite(linear_deflection) || linear_deflection < 0.001
      || !std::isfinite(angular_deflection) || angular_deflection < 0.01
      || angular_deflection > 1.0)
    throw std::runtime_error("STL deflection parameters are outside the supported range.");
  const TopoDS_Shape shape = ReadImportedBrep(data, size);
  ShapeJson(shape, 0);

  BRepMesh_IncrementalMesh mesher(shape, linear_deflection, false, angular_deflection, true);
  if (!mesher.IsDone()) throw std::runtime_error("OCCT could not mesh the B-rep for STL export.");
  StlAPI_Writer writer;
  writer.ASCIIMode() = false;
  std::ostringstream output(std::ios::out | std::ios::binary);
  if (!writer.Write(shape, output) || !output.good())
    throw std::runtime_error("OCCT could not write the STL export.");
  const std::string stl = output.str();
  if (stl.size() > kMaxDeliveryBytes)
    throw std::runtime_error("STL output exceeds the 64 MB delivery limit.");
  if (stl.size() < 84) throw std::runtime_error("OCCT returned a truncated binary STL export.");
  uint32_t triangle_count = 0;
  std::memcpy(&triangle_count, stl.data() + 80, sizeof(triangle_count));
  if (triangle_count == 0 || stl.size() != 84 + static_cast<size_t>(triangle_count) * 50)
    throw std::runtime_error("OCCT returned an invalid binary STL export.");

  TopoDS_Compound surface;
  BRep_Builder builder;
  builder.MakeCompound(surface);
  for (TopExp_Explorer explorer(shape, TopAbs_FACE); explorer.More(); explorer.Next())
    builder.Add(surface, explorer.Current());

  const size_t sample_count = std::min<size_t>(triangle_count, kStlDeviationSampleLimit);
  double max_deviation = 0.0;
  double squared_deviation_sum = 0.0;
  for (size_t sample = 0; sample < sample_count; ++sample) {
    const size_t triangle = sample * static_cast<size_t>(triangle_count) / sample_count;
    const char* vertices = stl.data() + 84 + triangle * 50 + 12;
    std::array<gp_Pnt, 3> points;
    for (size_t vertex = 0; vertex < 3; ++vertex) {
      float coordinates[3]{};
      std::memcpy(coordinates, vertices + vertex * 12, sizeof(coordinates));
      if (!std::isfinite(coordinates[0]) || !std::isfinite(coordinates[1])
          || !std::isfinite(coordinates[2]))
        throw std::runtime_error("OCCT returned a non-finite STL vertex.");
      points[vertex].SetCoord(coordinates[0], coordinates[1], coordinates[2]);
    }
    const gp_Pnt centroid((points[0].X() + points[1].X() + points[2].X()) / 3.0,
                          (points[0].Y() + points[1].Y() + points[2].Y()) / 3.0,
                          (points[0].Z() + points[1].Z() + points[2].Z()) / 3.0);
    const TopoDS_Shape vertex = BRepBuilderAPI_MakeVertex(centroid).Shape();
    BRepExtrema_DistShapeShape distance(vertex, surface, 1e-7);
    if (!distance.IsDone()) distance.Perform();
    if (!distance.IsDone()) throw std::runtime_error("OCCT could not measure STL surface deviation.");
    const double deviation = distance.Value();
    if (!std::isfinite(deviation))
      throw std::runtime_error("OCCT returned a non-finite STL surface deviation.");
    max_deviation = std::max(max_deviation, deviation);
    squared_deviation_sum += deviation * deviation;
  }

  std::ostringstream metrics;
  metrics.imbue(std::locale::classic());
  metrics << std::setprecision(17)
          << "{\"format\":\"binary_stl\",\"triangle_count\":" << triangle_count
          << ",\"linear_deflection\":" << linear_deflection
          << ",\"angular_deflection\":" << angular_deflection
          << ",\"surface_deviation_sample\":{\"method\":\"evenly sampled triangle centroids to exact B-rep faces\",\"sample_count\":"
          << sample_count << ",\"max\":" << max_deviation
          << ",\"rms\":" << std::sqrt(squared_deviation_sum / sample_count) << "}}";
  return {stl, metrics.str()};
}

TopoDS_Shape MakeProfileShape(const double* coordinates, size_t point_count,
                              int operation, int plane, double amount, bool centered) {
  if (!coordinates || point_count < 3)
    throw std::runtime_error("A profile needs at least 3 points.");
  if ((operation != 0 && operation != 1) || (plane != 0 && plane != 1))
    throw std::runtime_error("Unsupported profile operation or sketch plane.");
  if (!std::isfinite(amount) || amount <= 0)
    throw std::runtime_error("Operation amount must be finite and positive.");
  if (centered && operation != 0)
    throw std::runtime_error("Only profile extrusions can be centered.");
  if (operation == 1 && (plane != 1 || amount > kFullTurn + 1e-9))
    throw std::runtime_error("Revolution requires an XZ profile and an angle up to 360 degrees.");

  BRepBuilderAPI_MakePolygon polygon;
  double twice_area = 0;
  bool touches_axis = false;
  for (size_t i = 0; i < point_count; ++i) {
    const double x = coordinates[2 * i];
    const double y = coordinates[2 * i + 1];
    if (!std::isfinite(x) || !std::isfinite(y))
      throw std::runtime_error("Profile coordinates must be finite.");
    const size_t next = (i + 1) % point_count;
    const double next_x = coordinates[2 * next];
    const double next_y = coordinates[2 * next + 1];
    if (std::hypot(x - next_x, y - next_y) <= 1e-7)
      throw std::runtime_error("Profile edges must have distinct endpoints.");
    twice_area += x * next_y - next_x * y;
    if (std::abs(x) <= 1e-7) touches_axis = true;
    polygon.Add(plane == 0
      ? gp_Pnt(x, y, centered ? -amount / 2.0 : 0.0)
      : gp_Pnt(x, centered ? -amount / 2.0 : 0.0, y));
  }
  if (!std::isfinite(twice_area) || std::abs(twice_area) <= 1e-12)
    throw std::runtime_error("Profile area must be non-zero.");
  if (operation == 1) {
    for (size_t i = 0; i < point_count; ++i) {
      if (coordinates[2 * i] < -1e-7)
        throw std::runtime_error("Revolution profiles must stay on one side of the Z axis.");
    }
    if (!touches_axis)
      throw std::runtime_error("Revolution profile must touch the Z axis to form a solid.");
  }
  polygon.Close();
  if (!polygon.IsDone()) throw std::runtime_error("OCCT could not close the sketch profile.");
  BRepBuilderAPI_MakeFace face_builder(polygon.Wire());
  if (!face_builder.IsDone()) throw std::runtime_error("OCCT could not make a face from the sketch profile.");
  const TopoDS_Face face = face_builder.Face();
  if (!BRepCheck_Analyzer(face).IsValid())
    throw std::runtime_error("The sketch profile does not form a valid planar face.");

  if (operation == 0) {
    const gp_Vec direction = plane == 0 ? gp_Vec(0, 0, amount) : gp_Vec(0, amount, 0);
    BRepPrimAPI_MakePrism builder(face, direction, true, true);
    builder.Build();
    if (!builder.IsDone()) throw std::runtime_error("OCCT could not extrude the sketch profile.");
    return builder.Shape();
  }

  BRepPrimAPI_MakeRevol builder(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), amount, true);
  builder.Build();
  if (!builder.IsDone()) throw std::runtime_error("OCCT could not revolve the sketch profile.");
  return builder.Shape();
}

Handle(NCollection_HArray1<double>) MakePeriodicSplineParameters(const double* coordinates,
                                                                  size_t point_count) {
  if (!coordinates || point_count < 3
      || point_count >= static_cast<size_t>(std::numeric_limits<Standard_Integer>::max()))
    throw std::runtime_error("A periodic spline needs at least 3 points within the OCCT index range.");
  std::vector<double> segment_lengths;
  segment_lengths.reserve(point_count);
  double maximum_length = 0.0;
  for (size_t i = 0; i < point_count; ++i) {
    const size_t next = (i + 1) % point_count;
    const double dx = coordinates[2 * next] - coordinates[2 * i];
    const double dy = coordinates[2 * next + 1] - coordinates[2 * i + 1];
    const double length = std::hypot(dx, dy);
    if (!std::isfinite(length) || length <= 1e-9)
      throw std::runtime_error("Periodic spline vertices must be distinct and numerically stable.");
    segment_lengths.push_back(length);
    maximum_length = std::max(maximum_length, length);
  }
  Handle(NCollection_HArray1<double>) parameters = new NCollection_HArray1<double>(
      1, static_cast<Standard_Integer>(point_count + 1));
  double cumulative = 0.0;
  parameters->SetValue(1, cumulative);
  for (size_t i = 0; i < point_count; ++i) {
    cumulative += segment_lengths[i] / maximum_length;
    if (!std::isfinite(cumulative) || cumulative <= parameters->Value(static_cast<Standard_Integer>(i + 1)))
      throw std::runtime_error("Periodic spline parameter intervals are numerically unstable.");
    parameters->SetValue(static_cast<Standard_Integer>(i + 2), cumulative);
  }
  return parameters;
}

Handle(Geom_BSplineCurve) MakePeriodicSplineCurve(const double* coordinates, size_t point_count,
                                                   int plane,
                                                   Handle(NCollection_HArray1<double>)& parameters) {
  if (!coordinates || point_count < 3
      || point_count >= static_cast<size_t>(std::numeric_limits<Standard_Integer>::max()))
    throw std::runtime_error("A periodic spline needs at least 3 points within the OCCT index range.");
  if (plane != 0 && plane != 1)
    throw std::runtime_error("A periodic spline needs a supported sketch plane.");
  parameters = MakePeriodicSplineParameters(coordinates, point_count);
  Handle(NCollection_HArray1<gp_Pnt>) points = new NCollection_HArray1<gp_Pnt>(
      1, static_cast<Standard_Integer>(point_count));
  for (size_t i = 0; i < point_count; ++i) {
    const double x = coordinates[2 * i];
    const double y = coordinates[2 * i + 1];
    if (!std::isfinite(x) || !std::isfinite(y))
      throw std::runtime_error("Periodic spline coordinates must be finite.");
    points->SetValue(static_cast<Standard_Integer>(i + 1),
                     plane == 0 ? gp_Pnt(x, y, 0) : gp_Pnt(x, 0, y));
  }
  GeomAPI_Interpolate interpolator(points, parameters, true, 1e-9);
  interpolator.Perform();
  if (!interpolator.IsDone())
    throw std::runtime_error("OCCT could not interpolate the periodic B-spline.");
  const Handle(Geom_BSplineCurve) curve = interpolator.Curve();
  if (curve.IsNull() || !curve->IsPeriodic() || !curve->IsClosed())
    throw std::runtime_error("OCCT did not produce a closed periodic B-spline.");
  return curve;
}

std::string PeriodicSplineSamplesJson(const double* coordinates, size_t point_count,
                                     size_t samples_per_span) {
  if (samples_per_span == 0
      || point_count > (std::numeric_limits<size_t>::max() - 1) / samples_per_span)
    throw std::runtime_error("Periodic spline sample count is outside the native size range.");
  Handle(NCollection_HArray1<double>) parameters;
  const Handle(Geom_BSplineCurve) curve = MakePeriodicSplineCurve(coordinates, point_count, 0, parameters);
  const size_t sample_count = point_count * samples_per_span;
  std::ostringstream output;
  output.imbue(std::locale::classic());
  output << std::setprecision(17) << "{\"points\":[";
  for (size_t index = 0; index <= sample_count; ++index) {
    if (index) output << ',';
    double parameter = parameters->Value(static_cast<Standard_Integer>(point_count + 1));
    if (index < sample_count) {
      const size_t span = index / samples_per_span;
      const size_t step = index % samples_per_span;
      const double span_start = parameters->Value(static_cast<Standard_Integer>(span + 1));
      const double span_end = parameters->Value(static_cast<Standard_Integer>(span + 2));
      parameter = span_start + (span_end - span_start)
                                * static_cast<double>(step) / static_cast<double>(samples_per_span);
    }
    if (index == sample_count) {
      output << "{\"x\":" << coordinates[0] << ",\"y\":" << coordinates[1] << '}';
    } else if (index % samples_per_span == 0) {
      const size_t vertex = index / samples_per_span;
      output << "{\"x\":" << coordinates[2 * vertex]
             << ",\"y\":" << coordinates[2 * vertex + 1] << '}';
    } else {
      const gp_Pnt point = curve->Value(parameter);
      output << "{\"x\":" << point.X() << ",\"y\":" << point.Y() << '}';
    }
  }
  output << "]}";
  return output.str();
}

TopoDS_Shape MakeClosedSplineProfileShape(const double* coordinates, size_t point_count,
                                         int operation, int plane, double amount, bool centered) {
  if (!coordinates || point_count < 3)
    throw std::runtime_error("A closed spline profile needs at least 3 control vertices.");
  if ((operation != 0 && operation != 1) || (plane != 0 && plane != 1))
    throw std::runtime_error("Unsupported profile operation or sketch plane.");
  if (!std::isfinite(amount) || amount <= 0 || (centered && operation != 0))
    throw std::runtime_error("Operation amount must be finite and positive.");
  if (operation == 1 && (plane != 1 || amount > kFullTurn + 1e-9))
    throw std::runtime_error("Revolution requires an XZ profile and an angle up to 360 degrees.");

  using Point2 = std::array<double, 2>;
  std::vector<Point2> input;
  input.reserve(point_count);
  for (size_t i = 0; i < point_count; ++i) {
    Point2 point{coordinates[2 * i], coordinates[2 * i + 1]};
    if (!std::isfinite(point[0]) || !std::isfinite(point[1]))
      throw std::runtime_error("Profile coordinates must be finite.");
    input.push_back(point);
  }
  auto mix = [](const Point2& a, const Point2& b, double t) -> Point2 {
    return {a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t};
  };
  auto knot = [](const Point2& a, const Point2& b) -> double {
    const double value = std::sqrt(std::hypot(b[0] - a[0], b[1] - a[1]));
    if (!std::isfinite(value) || value <= 1e-12)
      throw std::runtime_error("Closed spline vertices must be distinct and numerically stable.");
    return value;
  };
  auto catmull = [&](size_t segment, double u) -> Point2 {
    const Point2& p0 = input[(segment + point_count - 1) % point_count];
    const Point2& p1 = input[segment];
    const Point2& p2 = input[(segment + 1) % point_count];
    const Point2& p3 = input[(segment + 2) % point_count];
    const double t0 = 0.0;
    const double t1 = t0 + knot(p0, p1);
    const double t2 = t1 + knot(p1, p2);
    const double t3 = t2 + knot(p2, p3);
    const double t = t1 + (t2 - t1) * u;
    const Point2 a1 = mix(p0, p1, (t - t0) / (t1 - t0));
    const Point2 a2 = mix(p1, p2, (t - t1) / (t2 - t1));
    const Point2 a3 = mix(p2, p3, (t - t2) / (t3 - t2));
    const Point2 b1 = mix(a1, a2, (t - t0) / (t2 - t0));
    const Point2 b2 = mix(a2, a3, (t - t1) / (t3 - t1));
    return mix(b1, b2, (t - t1) / (t2 - t1));
  };

  BRepBuilderAPI_MakeWire wire_builder;
  std::vector<Point2> sampled;
  if (point_count > sampled.max_size() / 32)
    throw std::runtime_error("Closed spline profile exceeds the supported geometry size.");
  sampled.reserve(point_count * 32);
  bool touches_axis = false;
  for (size_t segment = 0; segment < point_count; ++segment) {
    const Point2 p0 = catmull(segment, 0.0);
    const Point2 p1 = catmull(segment, 1.0 / 3.0);
    const Point2 p2 = catmull(segment, 2.0 / 3.0);
    const Point2 p3 = catmull(segment, 1.0);
    Point2 a{}, b{};
    for (size_t axis = 0; axis < 2; ++axis) {
      const double first = 27.0 * p1[axis] - 8.0 * p0[axis] - p3[axis];
      const double second = 27.0 * p2[axis] - p0[axis] - 8.0 * p3[axis];
      a[axis] = (2.0 * first - second) / 18.0;
      b[axis] = (2.0 * second - first) / 18.0;
      if (!std::isfinite(a[axis]) || !std::isfinite(b[axis]))
        throw std::runtime_error("Closed spline interpolation must remain finite.");
    }
    if (operation == 1) {
      const double cubic = -p0[0] + 3.0 * a[0] - 3.0 * b[0] + p3[0];
      const double quadratic = 3.0 * (p0[0] - 2.0 * a[0] + b[0]);
      const double linear = 3.0 * (a[0] - p0[0]);
      std::array<double, 4> candidates{p0[0], p3[0], std::numeric_limits<double>::quiet_NaN(),
                                       std::numeric_limits<double>::quiet_NaN()};
      const double qa = 3.0 * cubic;
      const double qb = 2.0 * quadratic;
      if (std::abs(qa) <= 1e-15) {
        if (std::abs(qb) > 1e-15) candidates[2] = -linear / qb;
      } else {
        const double discriminant = qb * qb - 4.0 * qa * linear;
        if (discriminant >= 0.0 && std::isfinite(discriminant)) {
          const double root = std::sqrt(discriminant);
          candidates[2] = (-qb - root) / (2.0 * qa);
          candidates[3] = (-qb + root) / (2.0 * qa);
        }
      }
      double minimum_x = std::min(p0[0], p3[0]);
      for (double u : candidates) {
        if (std::isfinite(u) && u > 0.0 && u < 1.0) {
          const double x = ((cubic * u + quadratic) * u + linear) * u + p0[0];
          minimum_x = std::min(minimum_x, x);
          if (std::abs(x) <= 1e-7) touches_axis = true;
        }
      }
      if (std::abs(p0[0]) <= 1e-7 || std::abs(p3[0]) <= 1e-7) touches_axis = true;
      if (minimum_x < -1e-7)
        throw std::runtime_error("Revolution profiles must stay on one side of the Z axis.");
    }
    NCollection_Array1<gp_Pnt> poles(1, 4);
    auto point3 = [plane](const Point2& p) {
      return plane == 0 ? gp_Pnt(p[0], p[1], 0) : gp_Pnt(p[0], 0, p[1]);
    };
    poles.SetValue(1, point3(p0));
    poles.SetValue(2, point3(a));
    poles.SetValue(3, point3(b));
    poles.SetValue(4, point3(p3));
    NCollection_Array1<double> knots(1, 2);
    knots.SetValue(1, 0.0);
    knots.SetValue(2, 1.0);
    NCollection_Array1<int> multiplicities(1, 2);
    multiplicities.SetValue(1, 4);
    multiplicities.SetValue(2, 4);
    Handle(Geom_BSplineCurve) curve = new Geom_BSplineCurve(poles, knots, multiplicities, 3, false);
    BRepBuilderAPI_MakeEdge edge(curve);
    if (!edge.IsDone()) throw std::runtime_error("OCCT could not create a closed spline profile edge.");
    wire_builder.Add(edge.Edge());
    for (size_t step = 0; step < 32; ++step) {
      const double u = static_cast<double>(step) / 32.0;
      const double v = 1.0 - u;
      Point2 point{
        v*v*v*p0[0] + 3*v*v*u*a[0] + 3*v*u*u*b[0] + u*u*u*p3[0],
        v*v*v*p0[1] + 3*v*v*u*a[1] + 3*v*u*u*b[1] + u*u*u*p3[1]};
      sampled.push_back(point);
    }
  }
  if (!wire_builder.IsDone()) throw std::runtime_error("OCCT could not close the spline profile wire.");
  double twice_area = 0.0;
  for (size_t i = 0; i < sampled.size(); ++i) {
    const Point2& a = sampled[i];
    const Point2& b = sampled[(i + 1) % sampled.size()];
    twice_area += a[0] * b[1] - b[0] * a[1];
  }
  if (!std::isfinite(twice_area) || std::abs(twice_area) <= 1e-12)
    throw std::runtime_error("Profile area must be non-zero.");
  if (operation == 1 && !touches_axis)
    throw std::runtime_error("Revolution profile must touch the Z axis to form a solid.");
  BRepBuilderAPI_MakeFace face_builder(wire_builder.Wire());
  if (!face_builder.IsDone()) throw std::runtime_error("OCCT could not make a face from the spline profile.");
  const TopoDS_Face face = face_builder.Face();
  if (!BRepCheck_Analyzer(face).IsValid())
    throw std::runtime_error("The closed spline does not form a valid planar face.");
  if (operation == 0) {
    const gp_Vec direction = plane == 0 ? gp_Vec(0, 0, amount) : gp_Vec(0, amount, 0);
    BRepPrimAPI_MakePrism builder(face, direction, true, true);
    builder.Build();
    if (!builder.IsDone()) throw std::runtime_error("OCCT could not extrude the closed spline profile.");
    return builder.Shape();
  }
  BRepPrimAPI_MakeRevol builder(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), amount, true);
  builder.Build();
  if (!builder.IsDone()) throw std::runtime_error("OCCT could not revolve the closed spline profile.");
  return builder.Shape();
}

TopoDS_Shape MakePeriodicSplineProfileShape(const double* coordinates, size_t point_count,
                                           int operation, int plane, double amount, bool centered) {
  if ((operation != 0 && operation != 1) || (plane != 0 && plane != 1))
    throw std::runtime_error("Unsupported profile operation or sketch plane.");
  if (!std::isfinite(amount) || amount <= 0 || (centered && operation != 0))
    throw std::runtime_error("Operation amount must be finite and positive.");
  if (operation == 1 && (plane != 1 || amount > kFullTurn + 1e-9))
    throw std::runtime_error("Revolution requires an XZ profile and an angle up to 360 degrees.");

  Handle(NCollection_HArray1<double>) parameters;
  const Handle(Geom_BSplineCurve) curve =
      MakePeriodicSplineCurve(coordinates, point_count, plane, parameters);
  BRepBuilderAPI_MakeEdge edge_builder(curve);
  if (!edge_builder.IsDone()) throw std::runtime_error("OCCT could not create the periodic spline edge.");
  BRepBuilderAPI_MakeWire wire_builder;
  wire_builder.Add(edge_builder.Edge());
  if (!wire_builder.IsDone()) throw std::runtime_error("OCCT could not close the periodic spline wire.");

  const size_t samples_per_span = 32;
  if (point_count > (std::numeric_limits<size_t>::max() - 1) / samples_per_span)
    throw std::runtime_error("Periodic spline profile exceeds the native sample range.");
  const size_t sample_count = point_count * samples_per_span;
  double twice_area = 0.0;
  gp_Pnt previous = curve->Value(parameters->Value(1));
  double minimum_radius = std::numeric_limits<double>::infinity();
  double maximum_radius = -std::numeric_limits<double>::infinity();
  for (size_t index = 0; index < point_count; ++index) {
    const double x = coordinates[2 * index];
    minimum_radius = std::min(minimum_radius, x);
    maximum_radius = std::max(maximum_radius, x);
  }
  constexpr double axis_tolerance = 1e-7;
  if (operation == 1 && minimum_radius < -axis_tolerance && maximum_radius > axis_tolerance)
    throw std::runtime_error("Revolution profiles must stay on one side of the Z axis.");
  const double radial_side = operation == 1 && maximum_radius < -axis_tolerance ? -1.0 : 1.0;
  for (size_t index = 1; index <= sample_count; ++index) {
    const size_t span = (index - 1) / samples_per_span;
    const size_t step = (index - 1) % samples_per_span + 1;
    const double span_start = parameters->Value(static_cast<Standard_Integer>(span + 1));
    const double span_end = parameters->Value(static_cast<Standard_Integer>(span + 2));
    const double parameter = span_start + (span_end - span_start)
                                          * static_cast<double>(step) / static_cast<double>(samples_per_span);
    const gp_Pnt current = curve->Value(parameter);
    const double x = current.X();
    const double y = plane == 0 ? current.Y() : current.Z();
    const double previous_y = plane == 0 ? previous.Y() : previous.Z();
    if (operation == 1 && radial_side * x < -axis_tolerance)
      throw std::runtime_error("Revolution profiles must stay on one side of the Z axis.");
    twice_area += previous.X() * y - x * previous_y;
    previous = current;
  }
  if (!std::isfinite(twice_area) || std::abs(twice_area) <= 1e-12)
    throw std::runtime_error("Profile area must be non-zero.");
  BRepBuilderAPI_MakeFace face_builder(wire_builder.Wire());
  if (!face_builder.IsDone()) throw std::runtime_error("OCCT could not make a face from the periodic spline.");
  const TopoDS_Face face = face_builder.Face();
  if (!BRepCheck_Analyzer(face).IsValid())
    throw std::runtime_error("The periodic spline does not form a valid planar face.");
  if (operation == 0) {
    const gp_Vec direction = plane == 0 ? gp_Vec(0, 0, amount) : gp_Vec(0, amount, 0);
    BRepPrimAPI_MakePrism builder(face, direction, true, true);
    builder.Build();
    if (!builder.IsDone()) throw std::runtime_error("OCCT could not extrude the periodic spline profile.");
    return builder.Shape();
  }
  BRepPrimAPI_MakeRevol builder(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), amount, true);
  builder.Build();
  if (!builder.IsDone()) throw std::runtime_error("OCCT could not revolve the periodic spline profile.");
  return builder.Shape();
}

TopoDS_Shape MakePipeShape(const double* coordinates, size_t point_count, int plane, double radius) {
  if (!coordinates || point_count < 3)
    throw std::runtime_error("A sweep path needs at least 3 points.");
  if (point_count > static_cast<size_t>(std::numeric_limits<Standard_Integer>::max()))
    throw std::runtime_error("Sweep path exceeds the OCCT point index range.");
  if ((plane != 0 && plane != 1) || !std::isfinite(radius) || radius <= 0)
    throw std::runtime_error("A pipe needs a supported sketch plane and a positive radius.");
  NCollection_Array1<gp_Pnt> points(1, static_cast<Standard_Integer>(point_count));
  gp_Pnt previous;
  for (size_t i = 0; i < point_count; ++i) {
    const double x = coordinates[2 * i];
    const double y = coordinates[2 * i + 1];
    if (!std::isfinite(x) || !std::isfinite(y))
      throw std::runtime_error("Sweep path coordinates must be finite.");
    const gp_Pnt point = plane == 0 ? gp_Pnt(x, y, 0) : gp_Pnt(x, 0, y);
    if (i && point.Distance(previous) <= 1e-7)
      throw std::runtime_error("Sweep path points must have distinct consecutive endpoints.");
    points.SetValue(static_cast<Standard_Integer>(i + 1), point);
    previous = point;
  }
  GeomAPI_PointsToBSpline spline_builder(points, 3, 8, GeomAbs_C2, 1e-3);
  if (!spline_builder.IsDone()) throw std::runtime_error("OCCT could not smooth the sweep path.");
  const Handle(Geom_BSplineCurve) spine_curve = spline_builder.Curve();
  BRepBuilderAPI_MakeEdge spine_edge(spine_curve);
  BRepBuilderAPI_MakeWire spine_builder;
  spine_builder.Add(spine_edge.Edge());
  if (!spine_builder.IsDone()) throw std::runtime_error("OCCT could not construct the sweep spine.");

  gp_Vec tangent(points.Value(1), points.Value(2));
  if (tangent.Magnitude() <= 1e-7) throw std::runtime_error("Sweep path tangent is degenerate.");
  tangent.Normalize();
  BRepBuilderAPI_MakeEdge section_edge(gp_Circ(gp_Ax2(points.Value(1), gp_Dir(tangent)), radius));
  BRepBuilderAPI_MakeWire section_builder;
  section_builder.Add(section_edge.Edge());
  if (!section_builder.IsDone()) throw std::runtime_error("OCCT could not construct the pipe section.");
  BRepBuilderAPI_MakeFace section_face(section_builder.Wire());
  if (!section_face.IsDone()) throw std::runtime_error("OCCT could not close the circular pipe section.");
  BRepOffsetAPI_MakePipe pipe(spine_builder.Wire(), section_face.Face());
  pipe.Build();
  if (!pipe.IsDone()) throw std::runtime_error("OCCT could not sweep the circular section along the path.");
  return pipe.Shape();
}

TopoDS_Shape MakeLoftShape(const double* coordinates, const int* section_sizes,
                           size_t section_count, size_t coordinate_count) {
  if (!coordinates || !section_sizes || section_count < 2)
    throw std::runtime_error("A loft needs at least two sketch sections.");
  BRepOffsetAPI_ThruSections loft(true, false, 1e-6);
  size_t offset = 0;
  for (size_t section = 0; section < section_count; ++section) {
    const int count = section_sizes[section];
    const double station = coordinates[offset * 3];
    if (!std::isfinite(station)) throw std::runtime_error("Loft section offset must be finite.");
    if (count < 3 || offset > coordinate_count || static_cast<size_t>(count) > coordinate_count - offset)
      throw std::runtime_error("Loft section coordinates are invalid.");
    BRepBuilderAPI_MakePolygon polygon;
    double twice_area = 0;
    for (int point = 0; point < count; ++point) {
      const size_t current = offset + static_cast<size_t>(point);
      const size_t next = offset + static_cast<size_t>((point + 1) % count);
      const double x = coordinates[3 * current + 1];
      const double z = coordinates[3 * current + 2];
      const double next_x = coordinates[3 * next + 1];
      const double next_z = coordinates[3 * next + 2];
      if (!std::isfinite(x) || !std::isfinite(z) || std::hypot(x - next_x, z - next_z) <= 1e-7)
        throw std::runtime_error("Loft section points must be finite and distinct.");
      twice_area += x * next_z - next_x * z;
      polygon.Add(gp_Pnt(x, station, z));
    }
    if (!std::isfinite(twice_area) || std::abs(twice_area) <= 1e-12)
      throw std::runtime_error("Loft section must enclose non-zero area.");
  // Section sketches are stored in XZ coordinates; the native loft consumes
  // xyz triples, with the section station supplied from a parallel offset buffer.
    polygon.Close();
    if (!polygon.IsDone()) throw std::runtime_error("OCCT could not close a loft section.");
    loft.AddWire(polygon.Wire());
    offset += static_cast<size_t>(count);
  }
  if (offset != coordinate_count)
    throw std::runtime_error("Loft coordinates do not match the section sizes.");
  loft.Build();
  if (!loft.IsDone()) throw std::runtime_error("OCCT could not build the loft through sketch sections.");
  return EnsureOneSolid(loft.Shape(), "Loft");
}

TopoDS_Edge MakeRuledRail(const double* coordinates, size_t point_count) {
  if (!coordinates || point_count < 2
      || point_count > static_cast<size_t>(std::numeric_limits<Standard_Integer>::max()))
    throw std::runtime_error("A ruled rail needs at least 2 points within the OCCT index range.");
  for (size_t i = 0; i < point_count * 3; ++i)
    if (!std::isfinite(coordinates[i]))
      throw std::runtime_error("Ruled rail coordinates must be finite.");
  if (point_count == 2) {
    const gp_Pnt start(coordinates[0], coordinates[1], coordinates[2]);
    const gp_Pnt end(coordinates[3], coordinates[4], coordinates[5]);
    if (start.Distance(end) <= 1e-7)
      throw std::runtime_error("Ruled rail endpoints must be distinct.");
    BRepBuilderAPI_MakeEdge edge(start, end);
    if (!edge.IsDone()) throw std::runtime_error("OCCT could not build a ruled rail edge.");
    return edge.Edge();
  }
  NCollection_Array1<gp_Pnt> points(1, static_cast<Standard_Integer>(point_count));
  for (size_t i = 0; i < point_count; ++i)
    points.SetValue(static_cast<Standard_Integer>(i + 1),
                    gp_Pnt(coordinates[3 * i], coordinates[3 * i + 1], coordinates[3 * i + 2]));
  GeomAPI_PointsToBSpline spline(points, 3, 8, GeomAbs_C2, 1e-4);
  if (!spline.IsDone()) throw std::runtime_error("OCCT could not interpolate a ruled rail.");
  BRepBuilderAPI_MakeEdge edge(spline.Curve());
  if (!edge.IsDone()) throw std::runtime_error("OCCT could not build an interpolated rail edge.");
  return edge.Edge();
}

TopoDS_Shape MakeRuledSolid(const double* coordinates, size_t coordinate_count,
                            const int* patch_sizes, size_t patch_count, double tolerance) {
  if (!coordinates || !patch_sizes || patch_count < 4
      || !std::isfinite(tolerance) || tolerance < 1e-7 || tolerance > 1.0)
    throw std::runtime_error("Ruled shell needs at least 4 patches and a sewing tolerance in [1e-7, 1].");
  BRepBuilderAPI_Sewing sewing(tolerance, true, true, true, false);
  size_t offset = 0;
  for (size_t patch = 0; patch < patch_count; ++patch) {
    const int first_count = patch_sizes[2 * patch];
    const int second_count = patch_sizes[2 * patch + 1];
    if (first_count < 2 || second_count < 2
        || offset > coordinate_count
        || static_cast<size_t>(first_count + second_count) * 3 > coordinate_count - offset)
      throw std::runtime_error("Ruled patch rail sizes do not match the coordinate buffer.");
    const TopoDS_Edge first = MakeRuledRail(coordinates + offset, static_cast<size_t>(first_count));
    offset += static_cast<size_t>(first_count) * 3;
    const TopoDS_Edge second = MakeRuledRail(coordinates + offset, static_cast<size_t>(second_count));
    offset += static_cast<size_t>(second_count) * 3;
    const TopoDS_Face face = BRepFill::Face(first, second);
    if (face.IsNull() || !BRepCheck_Analyzer(face).IsValid())
      throw std::runtime_error("OCCT could not build a valid ruled surface patch.");
    sewing.Add(face);
  }
  if (offset != coordinate_count)
    throw std::runtime_error("Ruled patch coordinates contain unused values.");
  sewing.Perform();
  const TopoDS_Shape sewed = sewing.SewedShape();
  if (sewed.IsNull() || sewing.NbFreeEdges() != 0 || sewing.NbMultipleEdges() != 0)
    throw std::runtime_error("Ruled surface patches did not sew into one closed manifold shell.");
  TopExp_Explorer shell_explorer(sewed, TopAbs_SHELL);
  if (!shell_explorer.More())
    throw std::runtime_error("Sewn ruled surfaces did not produce a shell.");
  const TopoDS_Shell shell = TopoDS::Shell(shell_explorer.Current());
  shell_explorer.Next();
  if (shell_explorer.More() || !BRep_Tool::IsClosed(shell))
    throw std::runtime_error("Sewn ruled surfaces must form one closed shell.");
  ShapeFix_Shell shell_fix(shell);
  shell_fix.FixFaceOrientation(shell);
  BRepBuilderAPI_MakeSolid solid_builder(shell_fix.Shell());
  if (!solid_builder.IsDone()) throw std::runtime_error("OCCT could not make a solid from the ruled shell.");
  ShapeFix_Solid solid_fix(solid_builder.Solid());
  solid_fix.SetPrecision(1e-7);
  solid_fix.SetMaxTolerance(tolerance);
  solid_fix.Perform();
  const TopoDS_Shape solid = solid_fix.Solid();
  if (!BRepCheck_Analyzer(solid).IsValid())
    throw std::runtime_error("Ruled shell did not produce a valid B-rep solid.");
  return EnsureOneSolid(solid, "Ruled shell");
}

TopoDS_Shape EnsureOneSolid(const TopoDS_Shape& shape, const char* operation) {
  size_t solids = 0;
  for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) ++solids;
  if (solids != 1) throw std::runtime_error(std::string(operation) + " must produce one connected solid.");
  return shape;
}

TopoDS_Solid ExtractSingleSolid(const TopoDS_Shape& shape) {
  TopExp_Explorer explorer(shape, TopAbs_SOLID);
  if (!explorer.More()) throw std::runtime_error("CAD delivery needs one closed solid.");
  const TopoDS_Solid solid = TopoDS::Solid(explorer.Current());
  explorer.Next();
  if (explorer.More()) throw std::runtime_error("CAD delivery needs exactly one closed solid.");
  if (!BRepCheck_Analyzer(solid).IsValid())
    throw std::runtime_error("CAD delivery solid is invalid.");
  return solid;
}

TopoDS_Shape MakeFillet(const TopoDS_Shape& source, const int* edge_indices, size_t edge_count, double radius) {
  if (!edge_indices || edge_count == 0 || !std::isfinite(radius) || radius <= 0)
    throw std::runtime_error("Fillet needs selected edges and a positive radius.");
  EnsureOneSolid(source, "Fillet");
  NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped_edges;
  TopExp::MapShapes(source, TopAbs_EDGE, mapped_edges);
  if (edge_count > static_cast<size_t>(mapped_edges.Extent()))
    throw std::runtime_error("Fillet edge selection exceeds the body edge count.");
  BRepFilletAPI_MakeFillet operation(source);
  std::unordered_set<int> selected;
  selected.reserve(edge_count);
  for (size_t i = 0; i < edge_count; ++i) {
    const int index = edge_indices[i];
    if (index < 0 || index >= mapped_edges.Extent())
      throw std::runtime_error("Fillet edge index is outside the current body topology.");
    if (!selected.insert(index).second)
      throw std::runtime_error("Fillet edge indices must be unique.");
    operation.Add(radius, TopoDS::Edge(mapped_edges.FindKey(index + 1)));
  }
  operation.Build();
  if (!operation.IsDone()) throw std::runtime_error("OCCT could not create the requested edge fillet.");
  return EnsureOneSolid(operation.Shape(), "Fillet");
}

TopoDS_Shape MakeFuse(const TopoDS_Shape& base, const TopoDS_Shape& tool) {
  BRepAlgoAPI_Fuse operation(base, tool);
  operation.SetFuzzyValue(1e-7);
  operation.Build();
  if (!operation.IsDone()) {
    std::ostringstream detail;
    operation.DumpErrors(detail);
    throw std::runtime_error("OCCT could not fuse the solids: " + detail.str());
  }
  return EnsureOneSolid(operation.Shape(), "Boolean fuse");
}

TopoDS_Shape MakeFuseParts(const TopoDS_Shape& base, const TopoDS_Shape& tool) {
  BRepAlgoAPI_Fuse operation(base, tool);
  operation.SetFuzzyValue(1e-7);
  operation.Build();
  if (!operation.IsDone()) {
    std::ostringstream detail;
    operation.DumpErrors(detail);
    throw std::runtime_error("OCCT could not fuse loft branches: " + detail.str());
  }
  return operation.Shape();
}

TopoDS_Shape MakeFusePartsBatch(const std::vector<TopoDS_Shape>& shapes,
                                const int* shape_indices, size_t shape_count) {
  if (!shape_indices || shape_count < 2)
    throw std::runtime_error("Multi-contour loft fusion needs at least two branch solids.");
  NCollection_List<TopoDS_Shape> arguments;
  NCollection_List<TopoDS_Shape> tools;
  arguments.Append(shapes[static_cast<size_t>(shape_indices[0])]);
  for (size_t i = 1; i < shape_count; ++i)
    tools.Append(shapes[static_cast<size_t>(shape_indices[i])]);
  BRepAlgoAPI_Fuse operation;
  operation.SetArguments(arguments);
  operation.SetTools(tools);
  operation.SetFuzzyValue(1e-7);
  operation.Build();
  if (!operation.IsDone()) {
    std::ostringstream detail;
    operation.DumpErrors(detail);
    throw std::runtime_error("OCCT could not batch-fuse loft branches: " + detail.str());
  }
  return operation.Shape();
}

TopoDS_Shape MakeCut(const TopoDS_Shape& base, const TopoDS_Shape& tool) {
  BRepAlgoAPI_Cut operation(base, tool);
  operation.SetFuzzyValue(1e-7);
  operation.Build();
  if (!operation.IsDone()) {
    std::ostringstream detail;
    operation.DumpErrors(detail);
    throw std::runtime_error("OCCT could not subtract the tool solid: " + detail.str());
  }
  return EnsureOneSolid(operation.Shape(), "Boolean cut");
}

TopoDS_Shape MakeMetricThread(double major_diameter, double pitch, double length, int handedness) {
  if (!std::isfinite(major_diameter) || !std::isfinite(pitch) || !std::isfinite(length)
      || major_diameter <= 0 || pitch <= 0 || length < pitch
      || (handedness != -1 && handedness != 1))
    throw std::runtime_error("Metric thread dimensions or handedness are outside the supported range.");

  const double major_radius = major_diameter / 2.0;
  const double thread_depth = (17.0 / 24.0) * (std::sqrt(3.0) / 2.0) * pitch;
  const double minor_radius = major_radius - thread_depth;
  if (!std::isfinite(minor_radius) || minor_radius <= 0)
    throw std::runtime_error("Metric thread pitch leaves no positive minor diameter.");

  // Overlap the swept ridge slightly with the core so the Boolean union has
  // volumetric intersection instead of coincident root faces.
  const double core_radius = minor_radius + pitch * 1e-5;
  const double core_start = 0.0;
  const double core_length = length;
  BRepPrimAPI_MakeCylinder core_builder(
      gp_Ax2(gp_Pnt(0, 0, core_start), gp_Dir(0, 0, 1)), core_radius, core_length);
  core_builder.Build();
  if (!core_builder.IsDone()) throw std::runtime_error("OCCT could not construct the metric thread core.");

  // ISO metric external design profile: 60-degree flank, P/8 crest flat,
  // h3 = 17H/24 = 0.613435P and R = sqrt(3)P/12 root rounds. The root rounds
  // are built into each loft section so they remain stable along the helix.
  const double root_half_width = 5.0 * pitch / 12.0;
  const double crest_half_width = pitch / 16.0;
  const double root_round = (std::sqrt(3.0) / 12.0) * pitch;
  const double root_tangent = std::sqrt(3.0) * root_round;
  const double root_tangent_radial = (std::sqrt(3.0) / 2.0) * root_tangent;
  const size_t root_arc_segments = 8;
  const double center_start = root_half_width;
  const double center_span = length - 2.0 * root_half_width;
  const double total_turns = center_span / pitch;
  const double section_count_value = std::ceil(total_turns * 24.0);
  const double max_section_count = static_cast<double>(std::numeric_limits<Standard_Integer>::max() - 1);
  if (!std::isfinite(section_count_value) || section_count_value < 2.0
      || section_count_value > max_section_count)
    throw std::runtime_error("Metric thread length exceeds the section count representable by Open CASCADE.");
  const size_t section_count = static_cast<size_t>(section_count_value);

  const double pi = std::acos(-1.0);
  BRepOffsetAPI_ThruSections loft(true, false);
  for (size_t index = 0; index <= section_count; ++index) {
    const double fraction = static_cast<double>(index) / static_cast<double>(section_count);
    const double center_z = center_start + fraction * center_span;
    const double theta = handedness * kFullTurn * center_z / pitch;
    const double radial_x = std::cos(theta);
    const double radial_y = std::sin(theta);
    const auto point = [&](double radius, double z_offset) {
      return gp_Pnt(radius * radial_x, radius * radial_y, center_z + z_offset);
    };
    BRepBuilderAPI_MakePolygon section;
    section.Add(point(minor_radius + root_tangent_radial, -root_half_width + root_tangent / 2.0));
    section.Add(point(major_radius, -crest_half_width));
    section.Add(point(major_radius, crest_half_width));
    section.Add(point(minor_radius + root_tangent_radial, root_half_width - root_tangent / 2.0));
    const double right_center_offset = root_half_width - root_tangent;
    for (size_t arc_step = 1; arc_step <= root_arc_segments; ++arc_step) {
      const double fraction_arc = static_cast<double>(arc_step) / root_arc_segments;
      const double angle = pi / 6.0 - (2.0 * pi / 3.0) * fraction_arc;
      section.Add(point(minor_radius + root_round + root_round * std::sin(angle),
                        right_center_offset + root_round * std::cos(angle)));
    }
    section.Add(point(minor_radius, -root_half_width + root_tangent));
    const double left_center_offset = -root_half_width + root_tangent;
    for (size_t arc_step = 1; arc_step < root_arc_segments; ++arc_step) {
      const double fraction_arc = static_cast<double>(arc_step) / root_arc_segments;
      const double angle = -pi / 2.0 - (2.0 * pi / 3.0) * fraction_arc;
      section.Add(point(minor_radius + root_round + root_round * std::sin(angle),
                        left_center_offset + root_round * std::cos(angle)));
    }
    section.Close();
    if (!section.IsDone()) throw std::runtime_error("OCCT could not construct a metric thread section.");
    loft.AddWire(section.Wire());
  }
  loft.Build();
  if (!loft.IsDone()) throw std::runtime_error("OCCT could not loft the metric thread helix.");

  return EnsureOneSolid(MakeFuse(core_builder.Shape(), loft.Shape()), "Metric thread");
}

TopoDS_Shape BuildGraphShape(const OperationNode* nodes, size_t node_count,
                             const double* coordinates, size_t coordinate_count,
                             const int* edge_indices, size_t edge_count,
                             const unsigned char* brep_data, size_t brep_size,
                             bool& allow_invalid_import) {
  constexpr size_t max_node_count = static_cast<size_t>(std::numeric_limits<int>::max()) + 1;
  if (!nodes || node_count == 0 || node_count > max_node_count)
    throw std::runtime_error("A geometry graph needs at least one operation and indices representable by the native bridge.");
  std::vector<TopoDS_Shape> shapes;
  shapes.reserve(node_count);
  for (size_t i = 0; i < node_count; ++i) {
    const OperationNode& node = nodes[i];
    if (node.edge_offset > edge_count || node.edge_count > edge_count - node.edge_offset)
      throw std::runtime_error("Geometry graph operation references data outside its edge buffer.");
    if (node.type == 6) {
      if (!brep_data || node.coordinate_count == 0
          || node.coordinate_offset > brep_size || node.coordinate_count > brep_size - node.coordinate_offset)
        throw std::runtime_error("Imported B-rep graph operation references data outside its buffer.");
    } else if (node.coordinate_offset > coordinate_count
        || node.coordinate_count > coordinate_count - node.coordinate_offset) {
      throw std::runtime_error("Geometry graph operation references data outside its coordinate buffer.");
    }
    const double* points = node.type != 6 && node.coordinate_count
                               ? coordinates + node.coordinate_offset : nullptr;
    const int* edges = node.edge_count ? edge_indices + node.edge_offset : nullptr;
    TopoDS_Shape shape;
    switch (node.type) {
      case 0:
        if (node.coordinate_count % 2 != 0)
          throw std::runtime_error("Profile graph coordinates must be paired x/y values.");
        shape = MakeProfileShape(points, node.coordinate_count / 2, node.operation, node.plane,
                                 node.amount, node.centered != 0);
        break;
      case 12:
        if (node.coordinate_count % 2 != 0)
          throw std::runtime_error("Closed spline profile coordinates must be paired x/y values.");
        shape = MakeClosedSplineProfileShape(points, node.coordinate_count / 2, node.operation,
                                             node.plane, node.amount, node.centered != 0);
        break;
      case 13:
        if (node.coordinate_count % 2 != 0)
          throw std::runtime_error("Periodic spline profile coordinates must be paired x/y values.");
        shape = MakePeriodicSplineProfileShape(points, node.coordinate_count / 2, node.operation,
                                               node.plane, node.amount, node.centered != 0);
        break;
      case 1:
        if (node.coordinate_count % 2 != 0)
          throw std::runtime_error("Sweep graph coordinates must be paired x/y values.");
        shape = MakePipeShape(points, node.coordinate_count / 2, node.plane, node.amount);
        break;
      case 2:
        if (node.input_a < 0 || node.input_b < 0
            || static_cast<size_t>(node.input_a) >= i || static_cast<size_t>(node.input_b) >= i)
          throw std::runtime_error("Boolean fuse graph references must point to earlier operations.");
        shape = MakeFuse(shapes[node.input_a], shapes[node.input_b]);
        break;
      case 7:
        if (node.input_a < 0 || node.input_b < 0
            || static_cast<size_t>(node.input_a) >= i || static_cast<size_t>(node.input_b) >= i)
          throw std::runtime_error("Boolean cut graph references must point to earlier operations.");
        shape = MakeCut(shapes[node.input_a], shapes[node.input_b]);
        break;
      case 8:
        if (node.edge_offset > edge_count || node.edge_count < 2 || node.edge_count > edge_count - node.edge_offset
            || node.coordinate_count % 3 != 0)
          throw std::runtime_error("Loft graph operation needs at least two section sizes.");
        shape = MakeLoftShape(points, edges, node.edge_count, node.coordinate_count / 3);
        break;
      case 9:
        if (node.input_a < 0 || node.input_b < 0
            || static_cast<size_t>(node.input_a) >= i || static_cast<size_t>(node.input_b) >= i)
          throw std::runtime_error("Multi-contour loft fuse references must point to earlier operations.");
        shape = MakeFuseParts(shapes[node.input_a], shapes[node.input_b]);
        break;
      case 10:
        if (node.edge_count < 2)
          throw std::runtime_error("Multi-contour loft batch fusion needs at least two branches.");
        for (size_t j = 0; j < node.edge_count; ++j) {
          if (edges[j] < 0 || static_cast<size_t>(edges[j]) >= i)
            throw std::runtime_error("Multi-contour loft batch references must point to earlier operations.");
        }
        shape = MakeFusePartsBatch(shapes, edges, node.edge_count);
        break;
      case 11:
        if (node.edge_count < 8 || node.edge_count % 2 != 0)
          throw std::runtime_error("Ruled shell graph needs at least four paired surface patches.");
        shape = MakeRuledSolid(points, node.coordinate_count, edges, node.edge_count / 2, node.amount);
        break;
      case 3:
        if (node.input_a < 0 || static_cast<size_t>(node.input_a) >= i)
          throw std::runtime_error("Fillet graph reference must point to an earlier operation.");
        shape = MakeFillet(shapes[node.input_a], edges, node.edge_count, node.amount);
        break;
      case 4:
        if (!points || node.coordinate_count != 3)
          throw std::runtime_error("Box graph operation needs three dimensions.");
        shape = BRepPrimAPI_MakeBox(points[0], points[1], points[2]).Shape();
        break;
      case 5:
        if (!points || node.coordinate_count != 2)
          throw std::runtime_error("Metric thread graph operation needs a major diameter and pitch.");
        shape = MakeMetricThread(points[0], points[1], node.amount, node.operation);
        break;
      case 6:
        shape = ReadImportedBrep(brep_data + node.coordinate_offset, node.coordinate_count);
        break;
      default:
        throw std::runtime_error("Unsupported geometry graph operation.");
    }
    shapes.push_back(shape);
  }
  allow_invalid_import = nodes[node_count - 1].type == 6;
  return shapes.back();
}

std::string BuildGraphJson(const OperationNode* nodes, size_t node_count,
                           const double* coordinates, size_t coordinate_count,
                           const int* edge_indices, size_t edge_count,
                           const unsigned char* brep_data, size_t brep_size,
                           int mesh, int topology) {
  bool allow_invalid_import = false;
  const TopoDS_Shape shape = BuildGraphShape(nodes, node_count, coordinates, coordinate_count,
      edge_indices, edge_count, brep_data, brep_size, allow_invalid_import);
  return ShapeJson(shape, mesh, topology, allow_invalid_import);
}

struct GeometryExportResult {
  std::string brep;
  std::string metrics;
};

GeometryExportResult SerializeGraphForExport(const OperationNode* nodes, size_t node_count,
                                              const double* coordinates, size_t coordinate_count,
                                              const int* edge_indices, size_t edge_count,
                                              const unsigned char* brep_data, size_t brep_size) {
  bool allow_invalid_import = false;
  const TopoDS_Shape shape = BuildGraphShape(nodes, node_count, coordinates, coordinate_count,
      edge_indices, edge_count, brep_data, brep_size, allow_invalid_import);
  if (allow_invalid_import && !BRepCheck_Analyzer(shape).IsValid())
    throw std::runtime_error("Invalid imported B-reps cannot be exported.");
  const TopoDS_Solid solid = ExtractSingleSolid(shape);
  const std::string metrics = ShapeJson(solid, 0, 0, false);
  std::ostringstream output;
  BRepTools::Write(solid, output, false, false, TopTools_FormatVersion_CURRENT);
  if (!output.good()) throw std::runtime_error("OCCT could not serialize the B-rep for export.");
  const std::string serialized = output.str();
  if (serialized.empty() || serialized.size() > 64 * 1024 * 1024)
    throw std::runtime_error("Serialized B-rep is empty or exceeds the 64 MB limit.");
  return {serialized, metrics};
}
}  // namespace

API char* cadyx_graph(const OperationNode* nodes, size_t node_count,
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

API char* cadyx_brep_export(const OperationNode* nodes, size_t node_count,
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

API char* cadyx_step_import(const unsigned char* data, size_t size,
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

API char* cadyx_section_profile(const unsigned char* data, size_t size, int normal_axis,
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

API char* cadyx_step_export(const unsigned char* data, size_t size, size_t* output_size,
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

API char* cadyx_stl_export(const unsigned char* data, size_t size,
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

API char* cadyx_modify_imported_brep(const unsigned char* data, size_t size, int operation,
                                    const double* parameters, size_t parameter_count,
                                    char* error, size_t capacity) noexcept {
  try {
    const size_t expected_count = operation == 1 ? 6 : operation == 2 ? 7 : 0;
    if (expected_count == 0 || parameter_count != expected_count || !parameters)
      throw std::runtime_error("Imported B-rep modification needs supported parameters.");
    const TopoDS_Shape source = ReadImportedBrep(data, size);
    TopoDS_Shape result;
    bool repaired = false;
    if (operation == 1) {
      repaired = !BRepCheck_Analyzer(source).IsValid();
      const TopoDS_Shape valid_source = RepairImportedBrep(source);
      result = ResizeImportedThreadX(valid_source, parameters);
    } else {
      result = TransformImportedBrep(source, parameters);
    }
    return CopyResult(ModifiedBrepJson(result, repaired));
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

API char* cadyx_box(double width, double height, double depth, int mesh,
                    char* error, size_t capacity) noexcept {
  try {
    if (!std::isfinite(width) || !std::isfinite(height) || !std::isfinite(depth)
        || width <= 0 || height <= 0 || depth <= 0)
      throw std::runtime_error("Box dimensions must be finite and positive.");
    BRepPrimAPI_MakeBox builder(width, height, depth);
    builder.Build();
    if (!builder.IsDone()) throw std::runtime_error("OCCT could not construct the solid.");
    return CopyResult(ShapeJson(builder.Shape(), mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

API char* cadyx_profile(const double* coordinates, size_t point_count, int operation,
                       int plane, double amount, int centered, int mesh,
                       char* error, size_t capacity) noexcept {
  try {
    return CopyResult(ShapeJson(MakeProfileShape(coordinates, point_count, operation, plane,
                                                 amount, centered != 0), mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

API char* cadyx_periodic_spline_samples(const double* coordinates, size_t point_count,
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

API char* cadyx_fuse_profiles(
    const double* base_coordinates, size_t base_point_count, int base_operation,
    int base_plane, double base_amount, int base_centered,
    const double* tool_coordinates, size_t tool_point_count, int tool_operation,
    int tool_plane, double tool_amount, int tool_centered, int mesh,
    char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Shape base = MakeProfileShape(base_coordinates, base_point_count,
        base_operation, base_plane, base_amount, base_centered != 0);
    const TopoDS_Shape tool = MakeProfileShape(tool_coordinates, tool_point_count,
        tool_operation, tool_plane, tool_amount, tool_centered != 0);
    BRepAlgoAPI_Fuse operation(base, tool);
    operation.Build();
    if (!operation.IsDone()) throw std::runtime_error("OCCT could not fuse the profile bodies.");
    const TopoDS_Shape fused = operation.Shape();
    size_t solids = 0;
    for (TopExp_Explorer explorer(fused, TopAbs_SOLID); explorer.More(); explorer.Next()) ++solids;
    if (solids != 1) throw std::runtime_error("Boolean fuse must produce one connected solid.");
    return CopyResult(ShapeJson(fused, mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT fuse failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}

API char* cadyx_brep_distance(const unsigned char* first_data, size_t first_size,
                              const unsigned char* second_data, size_t second_size,
                              NativeProgressCallback progress_callback, void* progress_user_data,
                              char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Solid first = ExtractSingleSolid(ReadImportedBrep(first_data, first_size));
    const TopoDS_Solid second = ExtractSingleSolid(ReadImportedBrep(second_data, second_size));
    if (!BRepCheck_Analyzer(first).IsValid() || !BRepCheck_Analyzer(second).IsValid())
      throw std::runtime_error("Clearance measurement requires valid closed solids.");
    constexpr double deflection = 1e-7;
    BRepExtrema_DistShapeShape distance;
    distance.LoadS1(first);
    distance.LoadS2(second);
    distance.SetDeflection(deflection);
    NativeProgressIndicator progress(progress_callback, progress_user_data);
    Message_ProgressScope whole(progress.Start(), "body_clearance", 100.0);
    progress.SetPhase("measure_distance");
    progress.Report(0.0, true);
    const bool distance_done = distance.Perform(whole.Next(55.0));
    if (!distance_done || !distance.IsDone() || distance.NbSolution() < 1)
      throw std::runtime_error("OCCT could not compute minimum distance between the solids.");
    progress.SetPhase("classify_interference");
    progress.Report(0.55, true);
    BRepAlgoAPI_Common common(first, second);
    common.SetFuzzyValue(deflection);
    common.SetRunParallel(true);
    common.Build(whole.Next(45.0));
    if (!common.IsDone())
      throw std::runtime_error("OCCT could not classify solid interference.");
    const TopoDS_Shape common_shape = common.Shape();
    const double intersection_volume = common_shape.IsNull() ? 0.0 : ShapeVolume(common_shape);
    if (!std::isfinite(intersection_volume) || intersection_volume < 0)
      throw std::runtime_error("OCCT returned an invalid solid intersection volume.");
    progress.SetPhase("completed");
    progress.Report(1.0, true);
    const double value = distance.Value();
    if (!std::isfinite(value) || value < 0)
      throw std::runtime_error("OCCT returned an invalid solid distance.");
    const gp_Pnt& first_point = distance.PointOnShape1(1);
    const gp_Pnt& second_point = distance.PointOnShape2(1);
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"minimum_distance\":" << value
           << ",\"deflection\":" << deflection
           << ",\"inner_solution\":" << (distance.InnerSolution() ? "true" : "false")
           << ",\"intersection_volume\":" << intersection_volume
           << ",\"solution_count\":" << distance.NbSolution()
           << ",\"point_on_first\":[" << first_point.X() << ',' << first_point.Y() << ',' << first_point.Z() << ']'
           << ",\"point_on_second\":[" << second_point.X() << ',' << second_point.Y() << ',' << second_point.Z() << "]}";
    return CopyResult(result.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT solid distance failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native distance failure.");
  }
  return nullptr;
}

API char* cadyx_brep_preservation(const unsigned char* reference_data, size_t reference_size,
                                 const unsigned char* candidate_data, size_t candidate_size,
                                 char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Solid reference = ExtractSingleSolid(ReadImportedBrep(reference_data, reference_size));
    const TopoDS_Solid candidate = ExtractSingleSolid(ReadImportedBrep(candidate_data, candidate_size));
    if (!BRepCheck_Analyzer(reference).IsValid() || !BRepCheck_Analyzer(candidate).IsValid())
      throw std::runtime_error("Preservation validation requires valid closed solids.");
    constexpr double deflection = 1e-7;
    BRepAlgoAPI_Common common(reference, candidate);
    common.SetFuzzyValue(deflection);
    common.SetRunParallel(true);
    common.Build();
    if (!common.IsDone()) throw std::runtime_error("OCCT could not compute the preserved solid volume.");

    const double reference_volume = ShapeVolume(reference);
    const double candidate_volume = ShapeVolume(candidate);
    const TopoDS_Shape common_shape = common.Shape();
    const double preserved_volume = common_shape.IsNull() ? 0.0 : ShapeVolume(common_shape);
    const double lost_volume = std::max(0.0, reference_volume - preserved_volume);
    const double added_volume = std::max(0.0, candidate_volume - preserved_volume);
    GProp_GProps reference_surface;
    GProp_GProps candidate_surface;
    BRepGProp::SurfaceProperties(reference, reference_surface);
    BRepGProp::SurfaceProperties(candidate, candidate_surface);
    const double volume_resolution = deflection * std::max(reference_surface.Mass(), candidate_surface.Mass());
    for (const double metric : {reference_volume, candidate_volume, preserved_volume,
                                lost_volume, added_volume, volume_resolution}) {
      if (!std::isfinite(metric) || metric < 0)
        throw std::runtime_error("OCCT returned invalid solid-preservation metrics.");
    }
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"deflection\":" << deflection
           << ",\"reference_volume\":" << reference_volume
           << ",\"candidate_volume\":" << candidate_volume
           << ",\"preserved_volume\":" << preserved_volume
           << ",\"lost_volume\":" << lost_volume
           << ",\"added_volume\":" << added_volume
           << ",\"estimated_volume_resolution\":" << volume_resolution << '}';
    return CopyResult(result.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT solid-preservation validation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native preservation failure.");
  }
  return nullptr;
}

API char* cadyx_brep_thickness(const unsigned char* data, size_t size,
                              NativeProgressCallback progress_callback, void* progress_user_data,
                              char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Solid solid = ExtractSingleSolid(ReadImportedBrep(data, size));
    if (!BRepCheck_Analyzer(solid).IsValid())
      throw std::runtime_error("Thickness sampling requires a valid closed solid.");
    Bnd_Box bounds;
    BRepBndLib::Add(solid, bounds, false);
    double xmin, ymin, zmin, xmax, ymax, zmax;
    bounds.Get(xmin, ymin, zmin, xmax, ymax, zmax);
    const double diagonal = gp_Pnt(xmin, ymin, zmin).Distance(gp_Pnt(xmax, ymax, zmax));
    if (!std::isfinite(diagonal) || diagonal <= Precision::Confusion())
      throw std::runtime_error("Thickness sampling requires non-degenerate solid bounds.");
    const double mesh_deflection = diagonal / 500.0;
    const double start_offset = std::max(Precision::Confusion() * 10.0, diagonal * 1e-8);
    NativeProgressIndicator progress(progress_callback, progress_user_data);
    Message_ProgressScope whole(progress.Start(), "wall_thickness_sampling", 100.0);
    IMeshTools_Parameters mesh_parameters;
    mesh_parameters.Deflection = mesh_deflection;
    mesh_parameters.Angle = 0.25;
    mesh_parameters.Relative = false;
    mesh_parameters.InParallel = true;
    progress.SetPhase("triangulate_surface");
    BRepMesh_IncrementalMesh mesher(solid, mesh_parameters, whole.Next(25.0));
    if (!mesher.IsDone()) throw std::runtime_error("OCCT could not triangulate the solid for thickness sampling.");
    progress.SetPhase("prepare_samples");
    progress.Report(0.25, true);
    IntCurvesFace_ShapeIntersector intersector;
    intersector.Load(solid, std::max(Precision::Confusion(), diagonal * 1e-9));
    BRepClass3d_SolidClassifier classifier(solid);
    double minimum = std::numeric_limits<double>::infinity();
    gp_Pnt minimum_point, minimum_opposite;
    size_t samples = 0, hits = 0, face_count = 0, triangle_count = 0;
    for (TopExp_Explorer count(solid, TopAbs_FACE); count.More(); count.Next()) ++face_count;
    std::vector<size_t> face_triangle_counts(face_count, 0);
    std::vector<size_t> active_faces;
    size_t counted_face = 0;
    for (TopExp_Explorer count(solid, TopAbs_FACE); count.More(); count.Next(), ++counted_face) {
      TopLoc_Location location;
      const auto triangulation = BRep_Tool::Triangulation(TopoDS::Face(count.Current()), location);
      if (!triangulation.IsNull()) {
        const size_t count_for_face = static_cast<size_t>(triangulation->NbTriangles());
        face_triangle_counts[counted_face] = count_for_face;
        triangle_count += count_for_face;
        if (count_for_face) active_faces.push_back(counted_face);
      }
    }
    const size_t sample_budget = std::min<size_t>(10000, triangle_count);
    std::vector<size_t> face_sample_quotas(face_count, 0);
    if (active_faces.size() <= sample_budget) {
      for (size_t index : active_faces) face_sample_quotas[index] = 1;
      size_t remaining = sample_budget - active_faces.size();
      const size_t distributable = triangle_count - active_faces.size();
      if (remaining && distributable) {
        std::vector<long double> remainders(face_count, 0.0L);
        std::vector<size_t> allocation_order = active_faces;
        size_t assigned = 0;
        for (size_t index : active_faces) {
          const size_t capacity = face_triangle_counts[index] - 1;
          const long double exact_share = static_cast<long double>(remaining) * capacity / distributable;
          const size_t extra = std::min(remaining - assigned,
                                        std::min(capacity, static_cast<size_t>(std::floor(exact_share))));
          face_sample_quotas[index] += extra;
          assigned += extra;
          remainders[index] = exact_share - extra;
        }
        std::sort(allocation_order.begin(), allocation_order.end(), [&](size_t left, size_t right) {
          return remainders[left] == remainders[right]
              ? left < right : remainders[left] > remainders[right];
        });
        size_t unassigned = remaining - assigned;
        while (unassigned) {
          bool allocated = false;
          for (size_t index : allocation_order) {
            if (face_sample_quotas[index] >= face_triangle_counts[index]) continue;
            ++face_sample_quotas[index];
            --unassigned;
            allocated = true;
            if (!unassigned) break;
          }
          if (!allocated) throw std::runtime_error("Thickness sample allocation exceeded available triangles.");
        }
      }
    } else {
      for (size_t sample = 0; sample < sample_budget; ++sample) {
        const long double position = (static_cast<long double>(sample) + 0.5L)
                                   * active_faces.size() / sample_budget;
        face_sample_quotas[active_faces[static_cast<size_t>(position)]] = 1;
      }
    }
    progress.Report(0.30, true);
    size_t face_index = 0;
    for (TopExp_Explorer explorer(solid, TopAbs_FACE); explorer.More(); explorer.Next(), ++face_index) {
      const TopoDS_Face face = TopoDS::Face(explorer.Current());
      TopLoc_Location location;
      const auto triangulation = BRep_Tool::Triangulation(face, location);
      if (!triangulation.IsNull()) {
        const size_t face_triangle_count = static_cast<size_t>(triangulation->NbTriangles());
        const size_t face_sample_quota = face_sample_quotas[face_index];
        for (size_t sample_index = 0; sample_index < face_sample_quota; ++sample_index) {
          const size_t local_triangle_index = ((2 * sample_index + 1) * face_triangle_count)
                                           / (2 * face_sample_quota);
          const int triangle_index = static_cast<int>(local_triangle_index + 1);
          int a, b, c;
          triangulation->Triangle(triangle_index).Get(a, b, c);
          const gp_Pnt pa = triangulation->Node(a).Transformed(location.Transformation());
          const gp_Pnt pb = triangulation->Node(b).Transformed(location.Transformation());
          const gp_Pnt pc = triangulation->Node(c).Transformed(location.Transformation());
          gp_Vec normal_vector = gp_Vec(pa, pb).Crossed(gp_Vec(pa, pc));
          if (face.Orientation() == TopAbs_REVERSED) normal_vector.Reverse();
          if (normal_vector.SquareMagnitude() <= Precision::SquareConfusion()) continue;
          const gp_Dir outward(normal_vector);
          const gp_Pnt point((pa.X() + pb.X() + pc.X()) / 3.0,
                             (pa.Y() + pb.Y() + pc.Y()) / 3.0,
                             (pa.Z() + pb.Z() + pc.Z()) / 3.0);
          gp_Dir inward = outward.Reversed();
          gp_Pnt start = point.Translated(gp_Vec(inward) * start_offset);
          classifier.Perform(start, start_offset * 0.1);
          if (classifier.State() != TopAbs_IN) {
            inward = outward;
            start = point.Translated(gp_Vec(inward) * start_offset);
            classifier.Perform(start, start_offset * 0.1);
            if (classifier.State() != TopAbs_IN) continue;
          }
          intersector.Perform(gp_Lin(start, inward), 0.0, diagonal * 2.0);
          ++samples;
          if (!intersector.IsDone())
            throw std::runtime_error("OCCT ray intersection failed during thickness sampling.");
          double nearest = std::numeric_limits<double>::infinity();
          gp_Pnt opposite;
          for (int hit = 1; hit <= intersector.NbPnt(); ++hit) {
            const double along = intersector.WParameter(hit);
            if (along > Precision::Confusion() && along < nearest) {
              nearest = along;
              opposite = intersector.Pnt(hit);
            }
          }
          if (std::isfinite(nearest)) {
            ++hits;
            const double thickness = nearest + start_offset;
            if (thickness < minimum) {
              minimum = thickness;
              minimum_point = point;
              minimum_opposite = opposite;
            }
          }
        }
      }
      progress.SetPhase("sample_surface_triangles");
      progress.Report(0.30 + (face_count ? 0.65 * static_cast<double>(face_index + 1) / face_count : 0.65));
    }
    if (samples == 0 || hits == 0 || !std::isfinite(minimum))
      throw std::runtime_error("OCCT found no valid inward-normal thickness samples.");
    progress.SetPhase("completed");
    progress.Report(1.0, true);
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"minimum_sampled_thickness\":" << minimum
           << ",\"mesh_deflection\":" << mesh_deflection
           << ",\"start_offset\":" << start_offset
           << ",\"sample_count\":" << samples
           << ",\"hit_count\":" << hits
           << ",\"point\":[" << minimum_point.X() << ',' << minimum_point.Y() << ',' << minimum_point.Z() << ']'
           << ",\"opposite_point\":[" << minimum_opposite.X() << ',' << minimum_opposite.Y() << ',' << minimum_opposite.Z() << ']'
           << '}';
    return CopyResult(result.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT thickness sampling failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native thickness sampling failure.");
  }
  return nullptr;
}
