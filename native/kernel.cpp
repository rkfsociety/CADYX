// Small Python-ABI-independent boundary; OCCT objects never leave this library.
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRepOffsetAPI_MakePipe.hxx>
#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <BRepBndLib.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <GProp_GProps.hxx>
#include <GeomAPI_PointsToBSpline.hxx>
#include <Geom_BSplineCurve.hxx>
#include <GeomAbs_CurveType.hxx>
#include <NCollection_Array1.hxx>
#include <TopExp_Explorer.hxx>
#include <TopExp.hxx>
#include <NCollection_IndexedMap.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopLoc_Location.hxx>
#include <Standard_Version.hxx>
#include <Standard_Failure.hxx>
#include <STEPControl_Reader.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <ShapeFix_Shape.hxx>
#include <algorithm>
#include <cstddef>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <iostream>
#include <locale>
#include <string>
#include <stdexcept>
#include <cstdint>
#include <vector>

#define API extern "C" __declspec(dllexport)
API const char* cadyx_kernel_version() noexcept { return OCC_VERSION_COMPLETE; }
API void cadyx_free(char* value) noexcept { delete[] value; }

namespace {
constexpr double kFullTurn = 6.28318530717958647692;

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
  BRepBndLib::Add(shape, bounds);
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

TopoDS_Shape MakeProfileShape(const double* coordinates, size_t point_count,
                              int operation, int plane, double amount, bool centered) {
  if (!coordinates || point_count < 3 || point_count > 4096)
    throw std::runtime_error("A profile needs between 3 and 4096 points.");
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

TopoDS_Shape MakePipeShape(const double* coordinates, size_t point_count, int plane, double radius) {
  if (!coordinates || point_count < 3 || point_count > 512)
    throw std::runtime_error("A sweep path needs between 3 and 512 points.");
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

TopoDS_Shape EnsureOneSolid(const TopoDS_Shape& shape, const char* operation) {
  size_t solids = 0;
  for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) ++solids;
  if (solids != 1) throw std::runtime_error(std::string(operation) + " must produce one connected solid.");
  return shape;
}

TopoDS_Shape MakeFillet(const TopoDS_Shape& source, const int* edge_indices, size_t edge_count, double radius) {
  if (!edge_indices || edge_count == 0 || edge_count > 128 || !std::isfinite(radius) || radius <= 0)
    throw std::runtime_error("Fillet needs selected edges and a positive radius.");
  EnsureOneSolid(source, "Fillet");
  NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped_edges;
  TopExp::MapShapes(source, TopAbs_EDGE, mapped_edges);
  BRepFilletAPI_MakeFillet operation(source);
  std::vector<int> selected;
  for (size_t i = 0; i < edge_count; ++i) {
    const int index = edge_indices[i];
    if (index < 0 || index >= mapped_edges.Extent())
      throw std::runtime_error("Fillet edge index is outside the current body topology.");
    if (std::find(selected.begin(), selected.end(), index) != selected.end())
      throw std::runtime_error("Fillet edge indices must be unique.");
    selected.push_back(index);
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

TopoDS_Shape MakeMetricThread(double major_diameter, double pitch, double length, int handedness) {
  if (!std::isfinite(major_diameter) || !std::isfinite(pitch) || !std::isfinite(length)
      || major_diameter <= 0 || pitch <= 0 || length < pitch || length / pitch > 64
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
  const size_t section_count = static_cast<size_t>(std::ceil(total_turns * 24.0));
  if (section_count < 2 || section_count > 1600)
    throw std::runtime_error("Metric thread loft exceeds the supported section count.");

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

std::string BuildGraphJson(const OperationNode* nodes, size_t node_count,
                           const double* coordinates, size_t coordinate_count,
                           const int* edge_indices, size_t edge_count,
                           const unsigned char* brep_data, size_t brep_size,
                           int mesh, int topology) {
  if (!nodes || node_count == 0 || node_count > 1024)
    throw std::runtime_error("A geometry graph needs between 1 and 1024 operations.");
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
  return ShapeJson(shapes.back(), mesh, topology, nodes[node_count - 1].type == 6);
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
