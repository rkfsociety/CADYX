#include "kernel_internal.hpp"

namespace cadyx::native {
char* BrepDistanceImpl(const unsigned char* first_data, size_t first_size,
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

char* BrepPreservationImpl(const unsigned char* reference_data, size_t reference_size,
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

char* BrepGlobalThicknessImpl(const unsigned char* data, size_t size,
                                     NativeProgressCallback progress_callback, void* progress_user_data,
                                     char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Solid solid = ExtractSingleSolid(ReadImportedBrep(data, size));
    if (!BRepCheck_Analyzer(solid).IsValid())
      throw std::runtime_error("Global thickness certification requires a valid closed solid.");
    size_t shell_count = 0;
    for (TopExp_Explorer explorer(solid, TopAbs_SHELL); explorer.More(); explorer.Next()) ++shell_count;

    NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped_vertices;
    TopExp::MapShapes(solid, TopAbs_VERTEX, mapped_vertices);
    if (shell_count != 1 || mapped_vertices.Extent() < 4)
      return CopyResult("{\"supported\":false,\"reason\":\"requires_one_convex_polyhedral_shell\"}");
    std::vector<gp_Pnt> vertices;
    vertices.reserve(static_cast<size_t>(mapped_vertices.Extent()));
    double max_shape_tolerance = 0.0;
    for (int index = 1; index <= mapped_vertices.Extent(); ++index) {
      const TopoDS_Vertex vertex = TopoDS::Vertex(mapped_vertices(index));
      vertices.push_back(BRep_Tool::Pnt(vertex));
      max_shape_tolerance = std::max(max_shape_tolerance, BRep_Tool::Tolerance(vertex));
    }

    std::vector<gp_Dir> face_directions;
    std::vector<gp_Dir> edge_directions;
    std::vector<gp_Pln> supporting_planes;
    for (TopExp_Explorer explorer(solid, TopAbs_FACE); explorer.More(); explorer.Next()) {
      const TopoDS_Face face = TopoDS::Face(explorer.Current());
      BRepAdaptor_Surface surface(face, true);
      if (surface.GetType() != GeomAbs_Plane)
        return CopyResult("{\"supported\":false,\"reason\":\"requires_planar_faces\"}");
      gp_Pln plane = surface.Plane();
      gp_Dir normal = plane.Axis().Direction();
      if (face.Orientation() == TopAbs_REVERSED) normal.Reverse();
      face_directions.push_back(normal);
      supporting_planes.push_back(plane);
      max_shape_tolerance = std::max(max_shape_tolerance, BRep_Tool::Tolerance(face));
    }
    if (supporting_planes.size() < 4)
      return CopyResult("{\"supported\":false,\"reason\":\"requires_one_convex_polyhedral_shell\"}");

    Bnd_Box bounds;
    BRepBndLib::Add(solid, bounds, false);
    double xmin, ymin, zmin, xmax, ymax, zmax;
    bounds.Get(xmin, ymin, zmin, xmax, ymax, zmax);
    const double diagonal = gp_Pnt(xmin, ymin, zmin).Distance(gp_Pnt(xmax, ymax, zmax));
    if (!std::isfinite(diagonal) || diagonal <= Precision::Confusion())
      throw std::runtime_error("Global thickness certification requires non-degenerate solid bounds.");
    const double convexity_tolerance = std::max(
        2.0 * max_shape_tolerance,
        diagonal * std::numeric_limits<double>::epsilon() * 128.0);
    NativeProgressIndicator progress(progress_callback, progress_user_data);
    progress.SetPhase("verify_convex_polyhedron");
    Message_ProgressScope whole(progress.Start(), "global_wall_thickness", 100.0);
    for (size_t face_index = 0; face_index < supporting_planes.size(); ++face_index) {
      const gp_Pln& plane = supporting_planes[face_index];
      const gp_Dir normal = face_directions[face_index];
      for (const gp_Pnt& vertex : vertices) {
        const double signed_distance = gp_Vec(plane.Location(), vertex).Dot(gp_Vec(normal));
        if (signed_distance > convexity_tolerance)
          return CopyResult("{\"supported\":false,\"reason\":\"requires_one_convex_polyhedral_shell\"}");
      }
      progress.Report(0.40 * static_cast<double>(face_index + 1) / supporting_planes.size());
    }

    NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher> mapped_edges;
    TopExp::MapShapes(solid, TopAbs_EDGE, mapped_edges);
    const double parallel_tolerance = std::max(Precision::Angular(), 1e-12);
    for (int index = 1; index <= mapped_edges.Extent(); ++index) {
      const TopoDS_Edge edge = TopoDS::Edge(mapped_edges(index));
      BRepAdaptor_Curve curve(edge);
      if (curve.GetType() != GeomAbs_Line)
        return CopyResult("{\"supported\":false,\"reason\":\"requires_linear_edges\"}");
      TopoDS_Vertex first_vertex, last_vertex;
      TopExp::Vertices(edge, first_vertex, last_vertex, true);
      if (first_vertex.IsNull() || last_vertex.IsNull())
        return CopyResult("{\"supported\":false,\"reason\":\"requires_linear_edges\"}");
      gp_Vec edge_vector(BRep_Tool::Pnt(first_vertex), BRep_Tool::Pnt(last_vertex));
      if (edge_vector.Magnitude() <= Precision::Confusion()) continue;
      const gp_Dir direction(edge_vector);
      if (std::none_of(edge_directions.begin(), edge_directions.end(), [&](const gp_Dir& known) {
            return direction.IsParallel(known, parallel_tolerance);
          })) edge_directions.push_back(direction);
    }

    struct WidthCandidate {
      double width = std::numeric_limits<double>::infinity();
      gp_Dir direction;
      gp_Pnt minimum_point;
      gp_Pnt maximum_point;
    } best;
    size_t candidate_count = 0;
    auto consider_direction = [&](const gp_Dir& direction) {
      ++candidate_count;
      double minimum_projection = std::numeric_limits<double>::infinity();
      double maximum_projection = -std::numeric_limits<double>::infinity();
      gp_Pnt minimum_point, maximum_point;
      for (const gp_Pnt& vertex : vertices) {
        const double value = vertex.X() * direction.X() + vertex.Y() * direction.Y() + vertex.Z() * direction.Z();
        if (value < minimum_projection) { minimum_projection = value; minimum_point = vertex; }
        if (value > maximum_projection) { maximum_projection = value; maximum_point = vertex; }
      }
      const double width = maximum_projection - minimum_projection;
      if (std::isfinite(width) && width < best.width) {
        best.width = width;
        best.direction = direction;
        best.minimum_point = minimum_point;
        best.maximum_point = maximum_point;
      }
    };
    progress.SetPhase("enumerate_support_directions");
    for (const gp_Dir& direction : face_directions) consider_direction(direction);
    const size_t pair_count = edge_directions.size() < 2
        ? 0 : edge_directions.size() * (edge_directions.size() - 1) / 2;
    size_t pairs_done = 0;
    double angular_error_bound = parallel_tolerance;
    for (size_t first = 0; first < edge_directions.size(); ++first) {
      for (size_t second = first + 1; second < edge_directions.size(); ++second) {
        gp_Vec cross = gp_Vec(edge_directions[first]).Crossed(gp_Vec(edge_directions[second]));
        const double cross_magnitude = cross.Magnitude();
        if (cross_magnitude > parallel_tolerance) {
          consider_direction(gp_Dir(cross));
          angular_error_bound = std::max(
              angular_error_bound, 64.0 * std::numeric_limits<double>::epsilon() / cross_magnitude);
        }
        ++pairs_done;
        if (pair_count)
          progress.Report(0.40 + 0.55 * static_cast<double>(pairs_done) / pair_count);
      }
    }
    if (!std::isfinite(best.width) || candidate_count == 0)
      throw std::runtime_error("OCCT could not enumerate global thickness support directions.");
    const double roundoff_bound = diagonal * std::numeric_limits<double>::epsilon()
                                * static_cast<double>(128 + 16 * (vertices.size() + candidate_count));
    const double error_bound = 2.0 * max_shape_tolerance + roundoff_bound
                             + diagonal * angular_error_bound;
    progress.SetPhase("completed");
    progress.Report(1.0, true);
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"supported\":true,\"certified\":true"
           << ",\"minimum_global_thickness\":" << best.width
           << ",\"minimum_bound\":" << std::max(0.0, best.width - error_bound)
           << ",\"maximum_bound\":" << best.width + error_bound
           << ",\"numerical_error_bound\":" << error_bound
           << ",\"face_count\":" << supporting_planes.size()
           << ",\"vertex_count\":" << vertices.size()
           << ",\"edge_direction_count\":" << edge_directions.size()
           << ",\"direction_count\":" << candidate_count
           << ",\"support_direction\":[" << best.direction.X() << ',' << best.direction.Y() << ',' << best.direction.Z() << ']'
           << ",\"minimum_support_point\":[" << best.minimum_point.X() << ',' << best.minimum_point.Y() << ',' << best.minimum_point.Z() << ']'
           << ",\"maximum_support_point\":[" << best.maximum_point.X() << ',' << best.maximum_point.Y() << ',' << best.maximum_point.Z() << ']'
           << '}';
    return CopyResult(result.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT global thickness certification failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native global thickness certification failure.");
  }
  return nullptr;
}

char* BrepThicknessImpl(const unsigned char* data, size_t size,
                              double minimum_thickness, double tolerance,
                              NativeProgressCallback progress_callback, void* progress_user_data,
                              char* error, size_t capacity) noexcept {
  try {
    if (!std::isfinite(minimum_thickness) || minimum_thickness <= 0.0
        || !std::isfinite(tolerance) || tolerance < 0.0)
      throw std::runtime_error("Thickness threshold must be positive and tolerance non-negative.");
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
    size_t samples = 0, hits = 0, violating_samples = 0, face_count = 0, sampled_face_count = 0,
           triangle_count = 0;
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
    std::vector<double> sampled_thicknesses;
    sampled_thicknesses.reserve(sample_budget);
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
      bool sampled_face = false;
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
          sampled_face = true;
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
            sampled_thicknesses.push_back(thickness);
            if (thickness + tolerance < minimum_thickness) ++violating_samples;
            if (thickness < minimum) {
              minimum = thickness;
              minimum_point = point;
              minimum_opposite = opposite;
            }
          }
        }
      }
      if (sampled_face) ++sampled_face_count;
      progress.SetPhase("sample_surface_triangles");
      progress.Report(0.30 + (face_count ? 0.65 * static_cast<double>(face_index + 1) / face_count : 0.65));
    }
    if (samples == 0 || hits == 0 || !std::isfinite(minimum))
      throw std::runtime_error("OCCT found no valid inward-normal thickness samples.");
    std::sort(sampled_thicknesses.begin(), sampled_thicknesses.end());
    const size_t middle_index = sampled_thicknesses.size() / 2;
    const double median = sampled_thicknesses.size() % 2
        ? sampled_thicknesses[middle_index]
        : sampled_thicknesses[middle_index - 1] / 2.0 + sampled_thicknesses[middle_index] / 2.0;
    const double p10 = sampled_thicknesses[(sampled_thicknesses.size() - 1) / 10];
    progress.SetPhase("completed");
    progress.Report(1.0, true);
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"minimum_sampled_thickness\":" << minimum
           << ",\"mesh_deflection\":" << mesh_deflection
           << ",\"start_offset\":" << start_offset
           << ",\"median_sampled_thickness\":" << median
           << ",\"p10_sampled_thickness\":" << p10
           << ",\"sample_count\":" << samples
           << ",\"face_count\":" << face_count
           << ",\"sampled_face_count\":" << sampled_face_count
           << ",\"hit_count\":" << hits
           << ",\"violating_sample_count\":" << violating_samples
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

}  // namespace cadyx::native
