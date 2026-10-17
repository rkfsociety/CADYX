#include "kernel_internal.hpp"

namespace cadyx::native {
std::string BoxJson(double width, double height, double depth, int mesh) {
  if (!std::isfinite(width) || !std::isfinite(height) || !std::isfinite(depth)
      || width <= 0 || height <= 0 || depth <= 0)
    throw std::runtime_error("Box dimensions must be finite and positive.");
  BRepPrimAPI_MakeBox builder(width, height, depth);
  builder.Build();
  if (!builder.IsDone()) throw std::runtime_error("OCCT could not construct the solid.");
  return ShapeJson(builder.Shape(), mesh, 0, false, nullptr);
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


}  // namespace cadyx::native
