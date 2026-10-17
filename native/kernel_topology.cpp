#include "kernel_internal.hpp"

namespace cadyx::native {
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

TopoDS_Shape MakeFillet(const TopoDS_Shape& source, const int* edge_indices, size_t edge_count, double radius,
                        std::vector<int>* generated_face_indices = nullptr) {
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
  const TopoDS_Shape result = EnsureOneSolid(operation.Shape(), "Fillet");
  if (generated_face_indices) {
    generated_face_indices->clear();
    NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> result_faces;
    TopExp::MapShapes(result, TopAbs_FACE, result_faces);
    for (size_t i = 0; i < edge_count; ++i) {
      const TopoDS_Edge source_edge = TopoDS::Edge(mapped_edges.FindKey(edge_indices[i] + 1));
      const auto generated = operation.Generated(source_edge);
      for (NCollection_List<TopoDS_Shape>::Iterator it(generated); it.More(); it.Next()) {
        const TopoDS_Shape& face = it.Value();
        if (face.ShapeType() == TopAbs_FACE && result_faces.Contains(face))
          generated_face_indices->push_back(result_faces.FindIndex(face) - 1);
      }
    }
    std::sort(generated_face_indices->begin(), generated_face_indices->end());
    generated_face_indices->erase(std::unique(generated_face_indices->begin(), generated_face_indices->end()),
                                  generated_face_indices->end());
  }
  return result;
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

std::string FuseProfilesJson(
    const double* base_coordinates, size_t base_point_count, int base_operation,
    int base_plane, double base_amount, int base_centered,
    const double* tool_coordinates, size_t tool_point_count, int tool_operation,
    int tool_plane, double tool_amount, int tool_centered, int mesh) {
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
  return ShapeJson(fused, mesh, 0, false, nullptr);
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
                             bool& allow_invalid_import,
                             std::vector<int>* generated_face_indices = nullptr) {
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
        shape = MakeFillet(shapes[node.input_a], edges, node.edge_count, node.amount,
                           generated_face_indices);
        break;
      case 4:
        if (!points || node.coordinate_count != 3)
          throw std::runtime_error("Box graph operation needs three dimensions.");
        shape = BRepPrimAPI_MakeBox(points[0], points[1], points[2]).Shape();
        break;
      case 14: {
        if (!points || node.coordinate_count != 3 || !std::isfinite(points[0]) || !std::isfinite(points[1])
            || !std::isfinite(points[2]) || points[2] <= 0 || !std::isfinite(node.amount) || node.amount <= 0
            || (node.plane != 0 && node.plane != 1))
          throw std::runtime_error("Analytic circular extrusion needs a finite center, positive radius/depth, and valid plane.");
        const double start = node.centered ? -node.amount / 2.0 : 0.0;
        const gp_Pnt origin = node.plane == 0 ? gp_Pnt(points[0], points[1], start)
                                               : gp_Pnt(points[0], start, points[1]);
        const gp_Dir direction = node.plane == 0 ? gp_Dir(0, 0, 1) : gp_Dir(0, 1, 0);
        BRepPrimAPI_MakeCylinder builder(gp_Ax2(origin, direction), points[2], node.amount);
        builder.Build();
        if (!builder.IsDone()) throw std::runtime_error("OCCT could not extrude an analytic sketch circle.");
        shape = builder.Shape();
        break;
      }
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
  std::vector<int> generated_face_indices;
  const TopoDS_Shape shape = BuildGraphShape(nodes, node_count, coordinates, coordinate_count,
      edge_indices, edge_count, brep_data, brep_size, allow_invalid_import, &generated_face_indices);
  return ShapeJson(shape, mesh, topology, allow_invalid_import, &generated_face_indices);
}

GeometryExportResult SerializeGraphForExport(const OperationNode* nodes, size_t node_count,
                                              const double* coordinates, size_t coordinate_count,
                                              const int* edge_indices, size_t edge_count,
                                              const unsigned char* brep_data, size_t brep_size) {
  bool allow_invalid_import = false;
  const TopoDS_Shape shape = BuildGraphShape(nodes, node_count, coordinates, coordinate_count,
      edge_indices, edge_count, brep_data, brep_size, allow_invalid_import, nullptr);
  if (allow_invalid_import && !BRepCheck_Analyzer(shape).IsValid())
    throw std::runtime_error("Invalid imported B-reps cannot be exported.");
  const TopoDS_Solid solid = ExtractSingleSolid(shape);
  const std::string metrics = ShapeJson(solid, 0, 0, false, nullptr);
  std::ostringstream output;
  BRepTools::Write(solid, output, false, false, TopTools_FormatVersion_CURRENT);
  if (!output.good()) throw std::runtime_error("OCCT could not serialize the B-rep for export.");
  const std::string serialized = output.str();
  if (serialized.empty() || serialized.size() > 64 * 1024 * 1024)
    throw std::runtime_error("Serialized B-rep is empty or exceeds the 64 MB limit.");
  return {serialized, metrics};
}

}  // namespace cadyx::native
