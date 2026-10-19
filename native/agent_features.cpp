// Agent-oriented native helpers for high-level holes, work-plane transforms and measurements.
#include "kernel_internal.hpp"

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakeCone.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_CurveType.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Vertex.hxx>
#include <Standard_Failure.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Cone.hxx>
#include <gp_Cylinder.hxx>
#include <gp_Dir.hxx>
#include <gp_Elips.hxx>
#include <gp_Lin.hxx>
#include <gp_Pln.hxx>
#include <gp_Pnt.hxx>
#include <gp_Mat.hxx>
#include <gp_Sphere.hxx>
#include <gp_Torus.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>

using namespace cadyx::native;

namespace {
constexpr size_t kMaxBrepBytes = 64 * 1024 * 1024;

TopoDS_Shape ReadBrep(const unsigned char* data, size_t size) {
  if (!data || size == 0 || size > kMaxBrepBytes)
    throw std::runtime_error("B-rep input is empty or exceeds the 64 MB limit.");
  std::istringstream input(std::string(reinterpret_cast<const char*>(data), size));
  TopoDS_Shape shape;
  BRep_Builder builder;
  BRepTools::Read(shape, input, builder);
  if (shape.IsNull()) throw std::runtime_error("OCCT could not read the B-rep input.");
  return shape;
}

TopoDS_Shape OneSolid(const TopoDS_Shape& shape, const char* operation) {
  if (shape.IsNull()) throw std::runtime_error(std::string(operation) + " returned a null shape.");
  if (!BRepCheck_Analyzer(shape).IsValid())
    throw std::runtime_error(std::string(operation) + " returned an invalid B-rep.");
  size_t count = 0;
  TopoDS_Shape result;
  for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) {
    result = explorer.Current();
    ++count;
  }
  if (count != 1)
    throw std::runtime_error(std::string(operation) + " must produce exactly one closed solid.");
  return result;
}

double Finite(const double* values, size_t count, size_t index, const char* name) {
  if (!values || index >= count || !std::isfinite(values[index]))
    throw std::runtime_error(std::string(name) + " must be finite.");
  return values[index];
}

gp_Dir Direction(const double* values, size_t count, size_t start, const char* name) {
  const double x = Finite(values, count, start, name);
  const double y = Finite(values, count, start + 1, name);
  const double z = Finite(values, count, start + 2, name);
  if (std::hypot(x, std::hypot(y, z)) <= 1e-12)
    throw std::runtime_error(std::string(name) + " must be non-zero.");
  return gp_Dir(x, y, z);
}

char* Serialize(const TopoDS_Shape& shape, size_t* output_size) {
  if (!output_size) throw std::runtime_error("Output-size pointer is required.");
  const TopoDS_Shape solid = OneSolid(shape, "Agent feature");
  std::ostringstream output;
  BRepTools::Write(solid, output);
  const std::string value = output.str();
  if (value.empty() || value.size() > kMaxBrepBytes)
    throw std::runtime_error("Serialized B-rep is empty or exceeds the 64 MB limit.");
  *output_size = value.size();
  return CopyBytes(value);
}

void WritePoint(std::ostringstream& json, const gp_Pnt& point) {
  json << '[' << point.X() << ',' << point.Y() << ',' << point.Z() << ']';
}

void WriteDirection(std::ostringstream& json, const gp_Dir& direction) {
  json << '[' << direction.X() << ',' << direction.Y() << ',' << direction.Z() << ']';
}

const char* SurfaceName(GeomAbs_SurfaceType type) {
  switch (type) {
    case GeomAbs_Plane: return "plane";
    case GeomAbs_Cylinder: return "cylinder";
    case GeomAbs_Cone: return "cone";
    case GeomAbs_Sphere: return "sphere";
    case GeomAbs_Torus: return "torus";
    case GeomAbs_BezierSurface: return "bezier";
    case GeomAbs_BSplineSurface: return "bspline";
    case GeomAbs_SurfaceOfRevolution: return "revolution";
    case GeomAbs_SurfaceOfExtrusion: return "extrusion";
    case GeomAbs_OffsetSurface: return "offset";
    case GeomAbs_OtherSurface: return "other";
  }
  return "other";
}

}  // namespace

// operation 1: conical countersink cut
//   values = origin xyz, axis xyz, outer diameter, inner diameter, depth
// operation 2: rigid transform
//   values = rotation axis xyz, angle radians, translation xyz
namespace cadyx::native {
char* AgentFeatureBrepImpl(const unsigned char* data, size_t size, int operation,
                                   const double* values, size_t value_count,
                                   size_t* output_size, char* error, size_t capacity) noexcept {
  if (output_size) *output_size = 0;
  try {
    const TopoDS_Shape source = OneSolid(ReadBrep(data, size), "Agent feature input");
    TopoDS_Shape result;

    if (operation == 1) {
      if (value_count != 9)
        throw std::runtime_error("Countersink needs origin, axis, two diameters and depth.");
      const gp_Pnt origin(Finite(values, value_count, 0, "Countersink origin"),
                          Finite(values, value_count, 1, "Countersink origin"),
                          Finite(values, value_count, 2, "Countersink origin"));
      const gp_Dir axis = Direction(values, value_count, 3, "Countersink axis");
      const double outer_diameter = Finite(values, value_count, 6, "Countersink diameter");
      const double inner_diameter = Finite(values, value_count, 7, "Hole diameter");
      const double depth = Finite(values, value_count, 8, "Countersink depth");
      if (outer_diameter <= inner_diameter || inner_diameter <= 0 || depth <= 0)
        throw std::runtime_error("Countersink diameters and depth are outside the supported range.");
      BRepPrimAPI_MakeCone tool(
          gp_Ax2(origin, axis), outer_diameter / 2.0, inner_diameter / 2.0, depth);
      tool.Build();
      if (!tool.IsDone()) throw std::runtime_error("OCCT could not construct the countersink tool.");
      BRepAlgoAPI_Cut cut(source, tool.Shape());
      cut.SetFuzzyValue(1e-7);
      cut.Build();
      if (!cut.IsDone()) throw std::runtime_error("OCCT could not cut the countersink.");
      result = OneSolid(cut.Shape(), "Countersink");
    } else if (operation == 2) {
      if (value_count != 7)
        throw std::runtime_error("Rigid transform needs axis, angle and translation.");
      const gp_Dir axis = Direction(values, value_count, 0, "Rotation axis");
      const double angle = Finite(values, value_count, 3, "Rotation angle");
      const gp_Vec translation(Finite(values, value_count, 4, "Translation"),
                               Finite(values, value_count, 5, "Translation"),
                               Finite(values, value_count, 6, "Translation"));
      gp_Trsf transform;
      transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), axis), angle);
      transform.SetTranslationPart(translation);
      BRepBuilderAPI_Transform operation_builder(source, transform, true);
      if (!operation_builder.IsDone())
        throw std::runtime_error("OCCT could not transform the agent feature.");
      result = OneSolid(operation_builder.Shape(), "Agent feature transform");
    } else {
      throw std::runtime_error("Unsupported agent feature operation.");
    }

    return Serialize(result, output_size);
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT agent feature failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown agent feature failure.");
  }
  return nullptr;
}

// Measure a serialized B-rep directly so Python does not have to compress it,
// reconstruct an imported_brep JSON node, and immediately decompress it again.
char* BrepMetricsImpl(const unsigned char* data, size_t size,
                             char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Shape shape = OneSolid(ReadBrep(data, size), "B-rep metrics");
    if (!BRepCheck_Analyzer(shape).IsValid())
      throw std::runtime_error("Mass properties require a valid closed B-rep solid.");
    GProp_GProps properties;
    BRepGProp::VolumeProperties(shape, properties);
    const double signed_volume = properties.Mass();
    const double volume = std::abs(signed_volume);
    if (!std::isfinite(volume) || volume <= 0.0)
      throw std::runtime_error("OCCT returned an invalid B-rep volume.");
    const gp_Pnt center = properties.CentreOfMass();
    const gp_Mat tensor = properties.MatrixOfInertia();
    const double orientation = signed_volume < 0 ? -1.0 : 1.0;
    for (double coordinate : {center.X(), center.Y(), center.Z()})
      if (!std::isfinite(coordinate))
        throw std::runtime_error("OCCT returned an invalid solid centroid.");
    for (int i = 1; i <= 3; ++i)
      for (int j = 1; j <= 3; ++j)
        if (!std::isfinite(tensor.Value(i, j)))
          throw std::runtime_error("OCCT returned an invalid inertia tensor.");
    std::ostringstream json;
    json.imbue(std::locale::classic());
    json << std::setprecision(17) << "{\"volume\":" << volume
         << ",\"center_of_mass\":[" << center.X() << ',' << center.Y() << ',' << center.Z()
         << "],\"inertia_unit_density\":[";
    for (int i = 1; i <= 3; ++i) {
      if (i != 1) json << ',';
      json << '[';
      for (int j = 1; j <= 3; ++j) {
        if (j != 1) json << ',';
        json << orientation * tensor.Value(i, j);
      }
      json << ']';
    }
    json << "]}";
    return CopyResult(json.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT B-rep metrics failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown B-rep metrics failure.");
  }
  return nullptr;
}

// Complete face metadata and analytic edge/face data are emitted together so
// Python can enrich semantic topology without parsing the same B-rep twice.
char* AgentTopologyImpl(const unsigned char* data, size_t size,
                               char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Shape shape = OneSolid(ReadBrep(data, size), "Topology input");
    std::ostringstream json;
    json.imbue(std::locale::classic());
    json << std::setprecision(17) << "{\"edges\":[";

    TopTools_IndexedMapOfShape edges;
    TopExp::MapShapes(shape, TopAbs_EDGE, edges);
    for (int index = 0; index < edges.Extent(); ++index) {
      if (index) json << ',';
      const TopoDS_Edge edge = TopoDS::Edge(edges.FindKey(index + 1));
      BRepAdaptor_Curve curve(edge);
      json << "{\"index\":" << index;

      TopoDS_Vertex first_vertex, last_vertex;
      TopExp::Vertices(edge, first_vertex, last_vertex, true);
      if (!first_vertex.IsNull() && !last_vertex.IsNull()) {
        json << ",\"start\":";
        WritePoint(json, BRep_Tool::Pnt(first_vertex));
        json << ",\"end\":";
        WritePoint(json, BRep_Tool::Pnt(last_vertex));
      }

      switch (curve.GetType()) {
        case GeomAbs_Line: {
          const gp_Lin line = curve.Line();
          json << ",\"origin\":";
          WritePoint(json, line.Location());
          json << ",\"direction\":";
          WriteDirection(json, line.Direction());
          break;
        }
        case GeomAbs_Circle: {
          const gp_Circ circle = curve.Circle();
          json << ",\"radius\":" << circle.Radius() << ",\"axis_origin\":";
          WritePoint(json, circle.Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, circle.Axis().Direction());
          break;
        }
        case GeomAbs_Ellipse: {
          const gp_Elips ellipse = curve.Ellipse();
          json << ",\"major_radius\":" << ellipse.MajorRadius()
               << ",\"minor_radius\":" << ellipse.MinorRadius()
               << ",\"axis_origin\":";
          WritePoint(json, ellipse.Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, ellipse.Axis().Direction());
          break;
        }
        default:
          break;
      }
      json << '}';
    }

    json << "],\"faces\":[";
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    for (int index = 0; index < faces.Extent(); ++index) {
      if (index) json << ',';
      const TopoDS_Face face = TopoDS::Face(faces.FindKey(index + 1));
      BRepAdaptor_Surface surface(face, true);
      GProp_GProps properties;
      BRepGProp::SurfaceProperties(face, properties);
      const double area = properties.Mass();
      const gp_Pnt center = properties.CentreOfMass();
      double u0 = 0.0, u1 = 0.0, v0 = 0.0, v1 = 0.0;
      BRepTools::UVBounds(face, u0, u1, v0, v1);

      json << "{\"index\":" << index
           << ",\"area\":" << area << ",\"center\":";
      WritePoint(json, center);
      json << ",\"surface\":\"" << SurfaceName(surface.GetType()) << "\""
           << ",\"uv_bounds\":[" << u0 << ',' << u1 << ',' << v0 << ',' << v1 << ']';

      switch (surface.GetType()) {
        case GeomAbs_Plane: {
          const gp_Pln plane = surface.Plane();
          json << ",\"origin\":";
          WritePoint(json, plane.Location());
          json << ",\"normal\":";
          WriteDirection(json, plane.Axis().Direction());
          break;
        }
        case GeomAbs_Cylinder: {
          const gp_Cylinder cylinder = surface.Cylinder();
          json << ",\"radius\":" << cylinder.Radius() << ",\"axis_origin\":";
          WritePoint(json, cylinder.Position().Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, cylinder.Axis().Direction());
          break;
        }
        case GeomAbs_Cone: {
          const gp_Cone cone = surface.Cone();
          json << ",\"reference_radius\":" << cone.RefRadius()
               << ",\"semi_angle\":" << cone.SemiAngle()
               << ",\"axis_origin\":";
          WritePoint(json, cone.Position().Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, cone.Axis().Direction());
          break;
        }
        case GeomAbs_Sphere: {
          const gp_Sphere sphere = surface.Sphere();
          json << ",\"radius\":" << sphere.Radius() << ",\"center\":";
          WritePoint(json, sphere.Position().Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, sphere.Position().Direction());
          break;
        }
        case GeomAbs_Torus: {
          const gp_Torus torus = surface.Torus();
          json << ",\"major_radius\":" << torus.MajorRadius()
               << ",\"minor_radius\":" << torus.MinorRadius()
               << ",\"center\":";
          WritePoint(json, torus.Position().Location());
          json << ",\"axis_direction\":";
          WriteDirection(json, torus.Position().Direction());
          break;
        }
        default:
          break;
      }
      json << '}';
    }
    json << "]}";
    return CopyResult(json.str());
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString()
        ? exception.GetMessageString() : "OCCT topology measurement failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown topology measurement failure.");
  }
  return nullptr;
}
}  // namespace cadyx::native
