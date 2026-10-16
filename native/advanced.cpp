// Advanced solid-modeling operations for CADYX. OCCT objects never leave this DLL.
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepFilletAPI_MakeChamfer.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepOffsetAPI_DraftAngle.hxx>
#include <BRepOffsetAPI_MakeOffsetShape.hxx>
#include <BRepOffsetAPI_MakeThickSolid.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BOPAlgo_Operation.hxx>
#include <NCollection_List.hxx>
#include <GeomAbs_JoinType.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Solid.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Dir.hxx>
#include <gp_Pln.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include <string>
#include <limits>
#include <vector>

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

TopoDS_Shape OneSolid(const TopoDS_Shape& shape, const char* operation) {
  if (shape.IsNull()) throw std::runtime_error(std::string(operation) + " returned a null shape.");
  if (!BRepCheck_Analyzer(shape).IsValid())
    throw std::runtime_error(std::string(operation) + " returned an invalid B-rep.");
  TopoDS_Shape only;
  size_t count = 0;
  for (TopExp_Explorer it(shape, TopAbs_SOLID); it.More(); it.Next()) {
    only = it.Current();
    ++count;
  }
  if (count != 1)
    throw std::runtime_error(std::string(operation) + " must produce exactly one closed solid.");
  return only;
}

char* Serialize(const TopoDS_Shape& shape, size_t* output_size) {
  if (!output_size) throw std::runtime_error("Output size pointer is required.");
  std::ostringstream output;
  BRepTools::Write(shape, output);
  const std::string value = output.str();
  if (value.empty() || value.size() > kMaxBrepBytes)
    throw std::runtime_error("Serialized B-rep is empty or exceeds the 64 MB limit.");
  char* result = new char[value.size()];
  std::memcpy(result, value.data(), value.size());
  *output_size = value.size();
  return result;
}

TopoDS_Edge EdgeAt(const TopoDS_Shape& shape, int zero_based) {
  if (zero_based < 0) throw std::runtime_error("Edge indices must be non-negative.");
  TopTools_IndexedMapOfShape edges;
  TopExp::MapShapes(shape, TopAbs_EDGE, edges);
  if (zero_based >= edges.Extent()) throw std::runtime_error("Edge index is outside current topology.");
  return TopoDS::Edge(edges.FindKey(zero_based + 1));
}

TopoDS_Face FaceAt(const TopoDS_Shape& shape, int zero_based) {
  if (zero_based < 0) throw std::runtime_error("Face indices must be non-negative.");
  TopTools_IndexedMapOfShape faces;
  TopExp::MapShapes(shape, TopAbs_FACE, faces);
  if (zero_based >= faces.Extent()) throw std::runtime_error("Face index is outside current topology.");
  return TopoDS::Face(faces.FindKey(zero_based + 1));
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

int PatternCount(const double* values, size_t value_count) {
  const double count_value = Finite(values, value_count, 0, "Pattern count");
  if (count_value < 2.0 || count_value > std::numeric_limits<int>::max()
      || std::abs(count_value - std::round(count_value)) > 1e-9)
    throw std::runtime_error("Pattern count must be an integer of at least 2 that fits the native integer range.");
  return static_cast<int>(std::llround(count_value));
}

template <typename CopyShape>
TopoDS_Shape FusePattern(const TopoDS_Shape& source, int count, CopyShape copy_shape) {
  NCollection_List<TopoDS_Shape> arguments;
  NCollection_List<TopoDS_Shape> tools;
  arguments.Append(source);
  for (int index = 1; index < count; ++index) tools.Append(copy_shape(index));
  BRepAlgoAPI_Fuse fuse;
  fuse.SetOperation(BOPAlgo_FUSE);
  fuse.SetArguments(arguments);
  fuse.SetTools(tools);
  fuse.Build();
  if (!fuse.IsDone()) throw std::runtime_error("OCCT pattern fuse failed.");
  return OneSolid(fuse.Shape(), "Pattern fuse");
}

TopoDS_Shape Transform(const TopoDS_Shape& source, const gp_Trsf& transform) {
  BRepBuilderAPI_Transform builder(source, transform, true);
  if (!builder.IsDone()) throw std::runtime_error("OCCT rigid transform failed.");
  return builder.Shape();
}

}  // namespace

// operation:
// 1 shell(thickness,tolerance; face indices to remove)
// 2 chamfer(distance; edge indices)
// 3 draft(angle_deg,pull xyz,neutral origin xyz,neutral normal xyz; face indices)
// 4 variable fillet(radius_start,radius_end; edge indices)
// 5 mirror(plane origin xyz, plane normal xyz)
// 6 linear pattern(count, dx,dy,dz) -- count includes source and copies are fused
// 7 circular pattern(count, origin xyz, axis xyz, angle_step_deg) -- fused
// 8 offset solid(offset,tolerance)
// 9 hole(origin xyz, axis xyz, diameter, depth)
// 10 boss(origin xyz, axis xyz, diameter, depth)
API char* cadyx_advanced_brep(const unsigned char* data, size_t size, int operation,
                              const double* values, size_t value_count,
                              const int* indices, size_t index_count,
                              size_t* output_size, char* generated_faces_json, size_t generated_faces_capacity,
                              char* error, size_t capacity) noexcept {
  try {
    const TopoDS_Shape source = OneSolid(ReadBrep(data, size), "Advanced operation input");
    TopoDS_Shape result;
    std::vector<int> generated_face_indices;

    if (operation == 1) {
      if (value_count != 2 || index_count == 0 || !indices)
        throw std::runtime_error("Shell needs thickness, tolerance and at least one face index.");
      const double thickness = Finite(values, value_count, 0, "Shell thickness");
      const double tolerance = Finite(values, value_count, 1, "Shell tolerance");
      if (std::abs(thickness) <= 1e-9 || tolerance <= 0)
        throw std::runtime_error("Shell thickness must be non-zero and tolerance positive.");
      TopTools_ListOfShape removed;
      for (size_t i = 0; i < index_count; ++i) removed.Append(FaceAt(source, indices[i]));
      BRepOffsetAPI_MakeThickSolid maker;
      maker.MakeThickSolidByJoin(source, removed, thickness, tolerance, BRepOffset_Skin,
                                 false, false, GeomAbs_Arc, true);
      if (!maker.IsDone()) throw std::runtime_error("OCCT shell operation failed.");
      result = maker.Shape();
    } else if (operation == 2) {
      if (value_count != 1 || index_count == 0 || !indices)
        throw std::runtime_error("Chamfer needs a distance and at least one edge index.");
      const double distance = Finite(values, value_count, 0, "Chamfer distance");
      if (distance <= 0) throw std::runtime_error("Chamfer distance must be positive.");
      BRepFilletAPI_MakeChamfer maker(source);
      for (size_t i = 0; i < index_count; ++i) maker.Add(distance, EdgeAt(source, indices[i]));
      maker.Build();
      if (!maker.IsDone()) throw std::runtime_error("OCCT chamfer operation failed.");
      result = maker.Shape();
      const TopoDS_Shape chamfered = OneSolid(result, "Chamfer");
      TopTools_IndexedMapOfShape faces;
      TopExp::MapShapes(chamfered, TopAbs_FACE, faces);
      for (size_t i = 0; i < index_count; ++i) {
        const auto generated = maker.Generated(EdgeAt(source, indices[i]));
        for (NCollection_List<TopoDS_Shape>::Iterator it(generated); it.More(); it.Next()) {
          if (it.Value().ShapeType() == TopAbs_FACE && faces.Contains(it.Value()))
            generated_face_indices.push_back(faces.FindIndex(it.Value()) - 1);
        }
      }
      std::sort(generated_face_indices.begin(), generated_face_indices.end());
      generated_face_indices.erase(std::unique(generated_face_indices.begin(), generated_face_indices.end()),
                                   generated_face_indices.end());
    } else if (operation == 3) {
      if (value_count != 10 || index_count == 0 || !indices)
        throw std::runtime_error("Draft needs angle, pull direction, neutral plane and face indices.");
      const double angle = Finite(values, value_count, 0, "Draft angle") * 3.14159265358979323846 / 180.0;
      if (std::abs(angle) <= 1e-9 || std::abs(angle) >= 1.5533430342749532)
        throw std::runtime_error("Draft angle magnitude must be between 0 and 89 degrees.");
      const gp_Dir pull = Direction(values, value_count, 1, "Draft pull direction");
      const gp_Pnt origin(Finite(values, value_count, 4, "Neutral origin"),
                          Finite(values, value_count, 5, "Neutral origin"),
                          Finite(values, value_count, 6, "Neutral origin"));
      const gp_Dir normal = Direction(values, value_count, 7, "Neutral normal");
      BRepOffsetAPI_DraftAngle maker(source);
      const gp_Pln neutral(origin, normal);
      for (size_t i = 0; i < index_count; ++i) maker.Add(FaceAt(source, indices[i]), pull, angle, neutral, false);
      maker.Build();
      if (!maker.IsDone()) throw std::runtime_error("OCCT draft operation failed.");
      result = maker.Shape();
    } else if (operation == 4) {
      if (value_count != 2 || index_count == 0 || !indices)
        throw std::runtime_error("Variable fillet needs two radii and edge indices.");
      const double first = Finite(values, value_count, 0, "First fillet radius");
      const double second = Finite(values, value_count, 1, "Second fillet radius");
      if (first <= 0 || second <= 0) throw std::runtime_error("Fillet radii must be positive.");
      BRepFilletAPI_MakeFillet maker(source);
      for (size_t i = 0; i < index_count; ++i) maker.Add(first, second, EdgeAt(source, indices[i]));
      maker.Build();
      if (!maker.IsDone()) throw std::runtime_error("OCCT variable fillet operation failed.");
      result = maker.Shape();
      const TopoDS_Shape filleted = OneSolid(result, "Variable fillet");
      TopTools_IndexedMapOfShape faces;
      TopExp::MapShapes(filleted, TopAbs_FACE, faces);
      for (size_t i = 0; i < index_count; ++i) {
        const auto generated = maker.Generated(EdgeAt(source, indices[i]));
        for (NCollection_List<TopoDS_Shape>::Iterator it(generated); it.More(); it.Next()) {
          if (it.Value().ShapeType() == TopAbs_FACE && faces.Contains(it.Value()))
            generated_face_indices.push_back(faces.FindIndex(it.Value()) - 1);
        }
      }
      std::sort(generated_face_indices.begin(), generated_face_indices.end());
      generated_face_indices.erase(std::unique(generated_face_indices.begin(), generated_face_indices.end()),
                                   generated_face_indices.end());
    } else if (operation == 5) {
      if (value_count != 6) throw std::runtime_error("Mirror needs plane origin and normal.");
      const gp_Pnt origin(Finite(values, value_count, 0, "Mirror origin"),
                          Finite(values, value_count, 1, "Mirror origin"),
                          Finite(values, value_count, 2, "Mirror origin"));
      const gp_Dir normal = Direction(values, value_count, 3, "Mirror normal");
      gp_Trsf transform;
      transform.SetMirror(gp_Ax2(origin, normal));
      result = Transform(source, transform);
    } else if (operation == 6) {
      if (value_count != 4) throw std::runtime_error("Linear pattern needs count and offset vector.");
      const int count = PatternCount(values, value_count);
      const gp_Vec step(Finite(values, value_count, 1, "Pattern offset"),
                        Finite(values, value_count, 2, "Pattern offset"),
                        Finite(values, value_count, 3, "Pattern offset"));
      if (step.Magnitude() <= 1e-9) throw std::runtime_error("Pattern offset must be non-zero.");
      result = FusePattern(source, count, [&](int index) {
        gp_Trsf transform;
        transform.SetTranslation(step * static_cast<double>(index));
        return Transform(source, transform);
      });
    } else if (operation == 7) {
      if (value_count != 8) throw std::runtime_error("Circular pattern needs count, axis and angle step.");
      const int count = PatternCount(values, value_count);
      const gp_Pnt origin(Finite(values, value_count, 1, "Pattern axis origin"),
                          Finite(values, value_count, 2, "Pattern axis origin"),
                          Finite(values, value_count, 3, "Pattern axis origin"));
      const gp_Dir axis = Direction(values, value_count, 4, "Pattern axis");
      const double step = Finite(values, value_count, 7, "Pattern angle step") * 3.14159265358979323846 / 180.0;
      if (std::abs(step) <= 1e-12) throw std::runtime_error("Pattern angle step must be non-zero.");
      result = FusePattern(source, count, [&](int index) {
        gp_Trsf transform;
        transform.SetRotation(gp_Ax1(origin, axis), step * static_cast<double>(index));
        return Transform(source, transform);
      });
    } else if (operation == 8) {
      if (value_count != 2) throw std::runtime_error("Offset solid needs offset and tolerance.");
      const double offset = Finite(values, value_count, 0, "Offset");
      const double tolerance = Finite(values, value_count, 1, "Offset tolerance");
      if (std::abs(offset) <= 1e-9 || tolerance <= 0)
        throw std::runtime_error("Offset must be non-zero and tolerance positive.");
      BRepOffsetAPI_MakeOffsetShape maker;
      maker.PerformByJoin(source, offset, tolerance, BRepOffset_Skin, false, false, GeomAbs_Arc, true);
      if (!maker.IsDone()) throw std::runtime_error("OCCT offset operation failed.");
      result = maker.Shape();
    } else if (operation == 9 || operation == 10) {
      if (value_count != 8) throw std::runtime_error("Hole/boss needs origin, axis, diameter and depth.");
      const gp_Pnt origin(Finite(values, value_count, 0, "Cylinder origin"),
                          Finite(values, value_count, 1, "Cylinder origin"),
                          Finite(values, value_count, 2, "Cylinder origin"));
      const gp_Dir axis = Direction(values, value_count, 3, "Cylinder axis");
      const double diameter = Finite(values, value_count, 6, "Diameter");
      const double depth = Finite(values, value_count, 7, "Depth");
      if (diameter <= 0 || depth <= 0) throw std::runtime_error("Diameter and depth must be positive.");
      const TopoDS_Shape cylinder = BRepPrimAPI_MakeCylinder(gp_Ax2(origin, axis), diameter / 2.0, depth).Shape();
      if (operation == 9) {
        BRepAlgoAPI_Cut cut(source, cylinder);
        cut.Build();
        if (!cut.IsDone()) throw std::runtime_error("OCCT hole cut failed.");
        result = cut.Shape();
      } else {
        BRepAlgoAPI_Fuse fuse(source, cylinder);
        fuse.Build();
        if (!fuse.IsDone()) throw std::runtime_error("OCCT boss fuse failed.");
        result = fuse.Shape();
      }
    } else {
      throw std::runtime_error("Unsupported advanced OCCT operation.");
    }

    result = OneSolid(result, "Advanced operation");
    if (operation == 2 || operation == 4) {
      if (!generated_faces_json || generated_faces_capacity == 0)
        throw std::runtime_error("Chamfer face metadata buffer is required.");
      std::ostringstream face_json;
      face_json << '[';
      for (size_t i = 0; i < generated_face_indices.size(); ++i)
        face_json << (i ? "," : "") << generated_face_indices[i];
      face_json << ']';
      const std::string face_value = face_json.str();
      if (face_value.size() >= generated_faces_capacity)
        throw std::runtime_error("Chamfer face metadata exceeds the supported buffer.");
      std::memcpy(generated_faces_json, face_value.c_str(), face_value.size() + 1);
    }
    return Serialize(result, output_size);
  } catch (const Standard_Failure& failure) {
    CopyError(error, capacity, failure.GetMessageString() ? failure.GetMessageString() : "OCCT advanced operation failed.");
  } catch (const std::exception& exc) {
    CopyError(error, capacity, exc.what());
  } catch (...) {
    CopyError(error, capacity, "Unknown advanced OCCT operation failure.");
  }
  return nullptr;
}
