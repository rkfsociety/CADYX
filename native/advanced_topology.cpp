// Face-level topology metadata for agent-side selection of shell/draft operations.
#include <BRepAdaptor_Surface.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <TopExp.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Pnt.hxx>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>

#define API extern "C" __declspec(dllexport)

namespace {
constexpr size_t kMaxBrepBytes = 64 * 1024 * 1024;

void CopyError(char* error, size_t capacity, const char* message) {
  if (!error || capacity == 0) return;
  const size_t count = std::min(std::strlen(message), capacity - 1);
  std::memcpy(error, message, count);
  error[count] = '\0';
}

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

char* CopyResult(const std::string& value) {
  char* result = new char[value.size() + 1];
  std::memcpy(result, value.c_str(), value.size() + 1);
  return result;
}
}  // namespace

API char* cadyx_face_topology(const unsigned char* data, size_t size,
                              char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Shape shape = ReadBrep(data, size);
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    std::ostringstream json;
    json.imbue(std::locale::classic());
    json << std::setprecision(17) << "{\"faces\":[";
    for (int index = 0; index < faces.Extent(); ++index) {
      if (index) json << ',';
      const TopoDS_Face face = TopoDS::Face(faces.FindKey(index + 1));
      GProp_GProps properties;
      BRepGProp::SurfaceProperties(face, properties);
      const double area = properties.Mass();
      const gp_Pnt center = properties.CentreOfMass();
      BRepAdaptor_Surface surface(face, true);
      double u0 = 0.0, u1 = 0.0, v0 = 0.0, v1 = 0.0;
      BRepTools::UVBounds(face, u0, u1, v0, v1);
      json << "{\"index\":" << index
           << ",\"area\":" << area
           << ",\"center\":[" << center.X() << ',' << center.Y() << ',' << center.Z() << ']'
           << ",\"surface\":\"" << SurfaceName(surface.GetType()) << "\""
           << ",\"uv_bounds\":[" << u0 << ',' << u1 << ',' << v0 << ',' << v1 << "]}";
    }
    json << "]}";
    return CopyResult(json.str());
  } catch (const std::exception& exc) {
    CopyError(error, capacity, exc.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown face topology failure.");
  }
  return nullptr;
}
