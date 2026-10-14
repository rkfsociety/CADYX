// Small Python-ABI-independent boundary; OCCT objects never leave this library.
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <gp_Ax1.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <GProp_GProps.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopLoc_Location.hxx>
#include <Standard_Version.hxx>
#include <Standard_Failure.hxx>
#include <algorithm>
#include <cstddef>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <locale>
#include <string>
#include <stdexcept>
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

std::string ShapeJson(const TopoDS_Shape& shape, int mesh) {
  if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid())
    throw std::runtime_error("OCCT constructed an invalid solid.");
  GProp_GProps properties;
  BRepGProp::VolumeProperties(shape, properties);
  const double volume = properties.Mass();
  if (!std::isfinite(volume) || volume <= 0)
    throw std::runtime_error("OCCT returned an invalid volume.");

  std::ostringstream json;
  json.imbue(std::locale::classic());
  json << std::setprecision(17) << "{\"volume\":" << volume;
  if (mesh) {
    BRepMesh_IncrementalMesh mesher(shape, 0.01, false, 0.5, false);
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
  json << "}";
  return json.str();
}

TopoDS_Shape MakeProfileShape(const double* coordinates, size_t point_count,
                              int operation, int plane, double amount) {
  if (!coordinates || point_count < 3 || point_count > 4096)
    throw std::runtime_error("A profile needs between 3 and 4096 points.");
  if ((operation != 0 && operation != 1) || (plane != 0 && plane != 1))
    throw std::runtime_error("Unsupported profile operation or sketch plane.");
  if (!std::isfinite(amount) || amount <= 0)
    throw std::runtime_error("Operation amount must be finite and positive.");
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
    polygon.Add(plane == 0 ? gp_Pnt(x, y, 0.0) : gp_Pnt(x, 0.0, y));
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
}  // namespace

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
                       int plane, double amount, int mesh,
                       char* error, size_t capacity) noexcept {
  try {
    return CopyResult(ShapeJson(MakeProfileShape(coordinates, point_count, operation, plane, amount), mesh));
  } catch (const Standard_Failure& exception) {
    CopyError(error, capacity, exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    CopyError(error, capacity, exception.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown native kernel failure.");
  }
  return nullptr;
}
