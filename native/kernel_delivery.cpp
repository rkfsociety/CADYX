#include "kernel_internal.hpp"

namespace cadyx::native {
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
  std::string json = ShapeJson(shape, 0, 0, false, nullptr);
  json.pop_back();
  json += std::string(",\"repaired\":") + (repaired ? "true" : "false")
      + ",\"brep_data\":\"" + Base64Encode(serialized) + "\"}";
  return json;
}

std::string ModifyImportedBrepJson(const unsigned char* data, size_t size, int operation,
                                  const double* parameters, size_t parameter_count) {
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
  return ModifiedBrepJson(result, repaired);
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
  std::string json = ShapeJson(shape, 0, 0, true, nullptr);
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
  ShapeJson(solid, 0, 0, false, nullptr);
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

StlExportResult ExportStl(const unsigned char* data, size_t size,
                          double linear_deflection, double angular_deflection) {
  if (!std::isfinite(linear_deflection) || linear_deflection < 0.001
      || !std::isfinite(angular_deflection) || angular_deflection < 0.01
      || angular_deflection > 1.0)
    throw std::runtime_error("STL deflection parameters are outside the supported range.");
  const TopoDS_Shape shape = ReadImportedBrep(data, size);
  ShapeJson(shape, 0, 0, false, nullptr);

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


}  // namespace cadyx::native
