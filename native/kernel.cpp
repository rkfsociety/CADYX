// Small Python-ABI-independent boundary; OCCT objects never leave this library.
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
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

API char* cadyx_box(double width, double height, double depth, int mesh,
                    char* error, size_t capacity) noexcept {
  const auto fail = [error, capacity](const char* message) {
    if (error && capacity) {
      const size_t count = std::min(std::strlen(message), capacity - 1);
      std::memcpy(error, message, count);
      error[count] = '\0';
    }
  };
  try {
    if (!std::isfinite(width) || !std::isfinite(height) || !std::isfinite(depth)
        || width <= 0 || height <= 0 || depth <= 0)
      throw std::runtime_error("Box dimensions must be finite and positive.");
    BRepPrimAPI_MakeBox builder(width, height, depth);
    builder.Build();
    if (!builder.IsDone()) throw std::runtime_error("OCCT could not construct the solid.");
    const TopoDS_Shape shape = builder.Shape();
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
    const std::string value = json.str();
    char* result = new char[value.size() + 1];
    std::memcpy(result, value.c_str(), value.size() + 1);
    return result;
  } catch (const Standard_Failure& exception) {
    fail(exception.GetMessageString() ? exception.GetMessageString() : "OCCT operation failed.");
  } catch (const std::exception& exception) {
    fail(exception.what());
  } catch (...) {
    fail("Unknown native kernel failure.");
  }
  return nullptr;
}
