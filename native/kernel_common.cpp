#include "kernel_internal.hpp"

namespace cadyx::native {
using NativeProgressCallback = void (*)(void*, const char*, double);

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

gp_Vec FaceNormalNearPoint(const TopoDS_Face& face, const gp_Pnt& point) {
  TopLoc_Location location;
  const auto triangulation = BRep_Tool::Triangulation(face, location);
  if (triangulation.IsNull()) return gp_Vec();
  double closest = std::numeric_limits<double>::infinity();
  gp_Vec nearest_normal;
  for (int index = 1; index <= triangulation->NbTriangles(); ++index) {
    int first, second, third;
    triangulation->Triangle(index).Get(first, second, third);
    const gp_Pnt a = triangulation->Node(first).Transformed(location.Transformation());
    const gp_Pnt b = triangulation->Node(second).Transformed(location.Transformation());
    const gp_Pnt c = triangulation->Node(third).Transformed(location.Transformation());
    const gp_Pnt center((a.X() + b.X() + c.X()) / 3.0,
                        (a.Y() + b.Y() + c.Y()) / 3.0,
                        (a.Z() + b.Z() + c.Z()) / 3.0);
    const double distance = center.SquareDistance(point);
    if (distance >= closest) continue;
    gp_Vec normal(a, b);
    normal.Cross(gp_Vec(a, c));
    if (face.Orientation() == TopAbs_REVERSED) normal.Reverse();
    if (normal.SquareMagnitude() <= 1e-24) continue;
    closest = distance;
    nearest_normal = normal;
  }
  if (nearest_normal.SquareMagnitude() > 1e-24) nearest_normal.Normalize();
  return nearest_normal;
}

std::string ShapeJson(const TopoDS_Shape& shape, int mesh, int topology = 0,
                      bool allow_invalid_import = false,
                      const std::vector<int>* generated_face_indices = nullptr) {
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
    // Fine linear and angular tolerances keep small curved details legible at
    // desktop viewport sizes without changing the source geometry.
    constexpr double kViewportLinearDeflection = 0.04;
    constexpr double kViewportAngularDeflection = 0.2;
    BRepMesh_IncrementalMesh mesher(shape, kViewportLinearDeflection, false,
                                    kViewportAngularDeflection, false);
    if (!mesher.IsDone()) throw std::runtime_error("OCCT could not tessellate the solid.");
    std::vector<double> positions;
    std::vector<size_t> triangles;
    std::vector<std::pair<size_t, size_t>> face_triangle_ranges;
    std::vector<double> edge_positions;
    std::vector<size_t> edge_lines;
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
      face_triangle_ranges.emplace_back((triangles.size() - static_cast<size_t>(triangulation->NbTriangles()) * 3) / 3,
                                        triangles.size() / 3);
    }
    if (triangles.empty()) throw std::runtime_error("OCCT returned an empty mesh.");
    NCollection_IndexedDataMap<TopoDS_Shape, NCollection_List<TopoDS_Shape>, TopTools_ShapeMapHasher>
        edge_faces;
    TopExp::MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces);
    for (int edge_index = 1; edge_index <= edge_faces.Extent(); ++edge_index) {
      const TopoDS_Edge edge = TopoDS::Edge(edge_faces.FindKey(edge_index));
      if (BRep_Tool::Degenerated(edge)) continue;
      const auto ancestors = edge_faces.FindFromKey(edge);
      bool seam = false;
      for (NCollection_List<TopoDS_Shape>::Iterator face_iterator(ancestors);
           face_iterator.More(); face_iterator.Next()) {
        if (BRep_Tool::IsClosed(edge, TopoDS::Face(face_iterator.Value()))) {
          seam = true;
          break;
        }
      }
      if (seam) continue;

      BRepAdaptor_Curve curve(edge);
      const double first = curve.FirstParameter();
      const double last = curve.LastParameter();
      if (!std::isfinite(first) || !std::isfinite(last) || first == last) continue;
      const gp_Pnt midpoint = curve.Value(first + (last - first) * 0.5);
      std::vector<gp_Vec> adjacent_normals;
      for (NCollection_List<TopoDS_Shape>::Iterator face_iterator(ancestors);
           face_iterator.More(); face_iterator.Next()) {
        const gp_Vec normal = FaceNormalNearPoint(TopoDS::Face(face_iterator.Value()), midpoint);
        if (normal.SquareMagnitude() > 1e-24) adjacent_normals.push_back(normal);
      }
      bool sharp = adjacent_normals.size() < 2;
      constexpr double kFeatureAngleCosine = 0.8660254037844386;
      for (size_t first_normal = 0; first_normal < adjacent_normals.size() && !sharp; ++first_normal) {
        for (size_t second_normal = first_normal + 1;
             second_normal < adjacent_normals.size(); ++second_normal) {
          if (adjacent_normals[first_normal].Dot(adjacent_normals[second_normal])
              < kFeatureAngleCosine) {
            sharp = true;
            break;
          }
        }
      }
      if (!sharp) continue;
      GCPnts_QuasiUniformDeflection samples(curve, kViewportLinearDeflection);
      if (!samples.IsDone() || samples.NbPoints() < 2)
        throw std::runtime_error("OCCT could not sample a B-rep edge for the viewport.");
      size_t previous = 0;
      for (int index = 1; index <= samples.NbPoints(); ++index) {
        const gp_Pnt point = curve.Value(samples.Parameter(index));
        if (!std::isfinite(point.X()) || !std::isfinite(point.Y()) || !std::isfinite(point.Z()))
          throw std::runtime_error("OCCT returned a non-finite B-rep edge vertex.");
        const size_t current = edge_positions.size() / 3;
        edge_positions.insert(edge_positions.end(), {point.X(), point.Y(), point.Z()});
        if (index > 1) edge_lines.insert(edge_lines.end(), {previous, current});
        previous = current;
      }
    }
    json << ",\"positions\":[";
    for (size_t i = 0; i < positions.size(); ++i) json << (i ? "," : "") << positions[i];
    json << "],\"triangles\":[";
    for (size_t i = 0; i < triangles.size(); ++i) json << (i ? "," : "") << triangles[i];
    json << "],\"edge_positions\":[";
    for (size_t i = 0; i < edge_positions.size(); ++i) json << (i ? "," : "") << edge_positions[i];
    json << "],\"edge_lines\":[";
    for (size_t i = 0; i < edge_lines.size(); ++i) json << (i ? "," : "") << edge_lines[i];
    json << "]";
    json << ",\"face_triangle_ranges\":[";
    for (size_t i = 0; i < face_triangle_ranges.size(); ++i) {
      if (i) json << ",";
      json << "{\"face_index\":" << i << ",\"start\":" << face_triangle_ranges[i].first
           << ",\"end\":" << face_triangle_ranges[i].second << "}";
    }
    json << "]";
    if (generated_face_indices) {
      json << ",\"generated_face_indices\":[";
      for (size_t i = 0; i < generated_face_indices->size(); ++i)
        json << (i ? "," : "") << (*generated_face_indices)[i];
      json << "]";
    }
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


}  // namespace cadyx::native
