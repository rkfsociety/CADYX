#define NOMINMAX
#include <open3d/Open3D.h>
#include <open3d/t/io/PointCloudIO.h>
#include <open3d/t/io/TriangleMeshIO.h>
#include <oneapi/tbb/global_control.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#if defined(_WIN32)
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C"
#endif

namespace {
using open3d::geometry::TriangleMesh;
using open3d::t::geometry::PointCloud;
using TensorTriangleMesh = open3d::t::geometry::TriangleMesh;

constexpr uint32_t kMaximumPoints = 100000;
constexpr uint32_t kMaximumInputVertices = 2000000;
constexpr uint32_t kMaximumInputTriangles = 5000000;
constexpr size_t kHeaderSize = 32;
using ProgressCallback = void (*)(int);

void ReportProgress(ProgressCallback callback, int phase) noexcept {
    if (callback) callback(phase);
}

void SetError(char* error, size_t capacity, const char* message) noexcept {
    if (!error || capacity == 0) return;
    const size_t count = std::min(std::strlen(message), capacity - 1);
    std::memcpy(error, message, count);
    error[count] = '\0';
}

void AppendU32(std::vector<uint8_t>& output, uint32_t value) {
    output.push_back(static_cast<uint8_t>(value));
    output.push_back(static_cast<uint8_t>(value >> 8));
    output.push_back(static_cast<uint8_t>(value >> 16));
    output.push_back(static_cast<uint8_t>(value >> 24));
}

void AppendF32(std::vector<uint8_t>& output, float value) {
    uint32_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value));
    std::memcpy(&bits, &value, sizeof(bits));
    AppendU32(output, bits);
}

unsigned int ProcessingThreads() noexcept {
    const unsigned int available = std::max(1u, std::thread::hardware_concurrency());
    return std::min(4u, std::max(1u, (available + 1u) / 2u));
}

void CleanMesh(TriangleMesh& mesh) {
    mesh.RemoveDuplicatedVertices()
            .RemoveDuplicatedTriangles()
            .RemoveDegenerateTriangles()
            .RemoveNonManifoldEdges()
            .RemoveUnreferencedVertices();
}

std::vector<uint8_t> SerializeMesh(const TriangleMesh& mesh,
                                   uint32_t source_kind,
                                   uint32_t input_vertices,
                                   uint32_t input_triangles,
                                   float effective_voxel_size) {
    if (mesh.vertices_.empty() || mesh.triangles_.empty()) {
        throw std::runtime_error("Scan processing produced an empty triangle mesh.");
    }
    if (mesh.vertices_.size() > std::numeric_limits<uint32_t>::max() ||
        mesh.triangles_.size() > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("Processed scan exceeds the supported mesh size.");
    }

    const auto vertex_count = static_cast<uint32_t>(mesh.vertices_.size());
    const auto triangle_count = static_cast<uint32_t>(mesh.triangles_.size());
    std::vector<uint8_t> output;
    output.reserve(kHeaderSize + static_cast<size_t>(vertex_count) * 12 +
                   static_cast<size_t>(triangle_count) * 12);
    output.insert(output.end(), {'C', 'Y', 'S', 'M'});
    AppendU32(output, 1);
    AppendU32(output, vertex_count);
    AppendU32(output, triangle_count);
    AppendU32(output, source_kind);
    AppendU32(output, input_vertices);
    AppendU32(output, input_triangles);
    AppendF32(output, effective_voxel_size);

    for (const auto& vertex : mesh.vertices_) {
        for (int axis = 0; axis < 3; ++axis) {
            const double coordinate = vertex[axis];
            if (!std::isfinite(coordinate) ||
                std::abs(coordinate) > std::numeric_limits<float>::max()) {
                throw std::runtime_error("Processed scan contains an invalid vertex.");
            }
            AppendF32(output, static_cast<float>(coordinate));
        }
    }
    for (const auto& triangle : mesh.triangles_) {
        for (int axis = 0; axis < 3; ++axis) {
            const int index = triangle[axis];
            if (index < 0 || static_cast<uint32_t>(index) >= vertex_count) {
                throw std::runtime_error("Processed scan contains an invalid triangle index.");
            }
            AppendU32(output, static_cast<uint32_t>(index));
        }
    }
    return output;
}

std::shared_ptr<TriangleMesh> ReadAndSimplifyMesh(const std::string& filename,
                                                  double voxel_size,
                                                  int target_triangles,
                                                  uint32_t& input_vertices,
                                                  uint32_t& input_triangles,
                                                  ProgressCallback progress) {
    ReportProgress(progress, 1);
    TensorTriangleMesh tensor_mesh;
    if (!open3d::t::io::ReadTriangleMesh(filename, tensor_mesh) ||
        !tensor_mesh.HasTriangleIndices() || tensor_mesh.GetTriangleIndices().GetLength() == 0) {
        return nullptr;
    }
    const size_t vertex_count = static_cast<size_t>(tensor_mesh.GetVertexPositions().GetLength());
    const size_t triangle_count = static_cast<size_t>(tensor_mesh.GetTriangleIndices().GetLength());
    if (vertex_count > kMaximumInputVertices || triangle_count > kMaximumInputTriangles) {
        throw std::runtime_error("PLY mesh exceeds the 2M vertex or 5M triangle processing limit.");
    }
    input_vertices = static_cast<uint32_t>(vertex_count);
    input_triangles = static_cast<uint32_t>(triangle_count);

    auto mesh = std::make_shared<TriangleMesh>(tensor_mesh.ToLegacy());
    ReportProgress(progress, 2);
    CleanMesh(*mesh);
    if (mesh->triangles_.empty()) {
        throw std::runtime_error("PLY mesh has no usable triangles after cleanup.");
    }

    // Voxel clustering bounds the cost of QEM on dense scanner exports.
    ReportProgress(progress, 7);
    if (mesh->triangles_.size() > static_cast<size_t>(target_triangles) * 2) {
        auto clustered = mesh->SimplifyVertexClustering(
                voxel_size, TriangleMesh::SimplificationContraction::Average);
        if (!clustered) throw std::runtime_error("Open3D could not cluster the scan mesh.");
        mesh = std::move(clustered);
        CleanMesh(*mesh);
    }
    if (mesh->triangles_.size() > static_cast<size_t>(target_triangles)) {
        auto simplified = mesh->SimplifyQuadricDecimation(
                target_triangles, std::numeric_limits<double>::max(), 1.0);
        if (!simplified) throw std::runtime_error("Open3D could not simplify the scan mesh.");
        mesh = std::move(simplified);
        CleanMesh(*mesh);
    }
    return mesh;
}

std::shared_ptr<TriangleMesh> ReconstructPointCloud(const std::string& filename,
                                                    double requested_voxel_size,
                                                    int target_triangles,
                                                    int outlier_neighbors,
                                                    double outlier_std_ratio,
                                                    int poisson_depth,
                                                    double poisson_density_quantile,
                                                    uint32_t& input_vertices,
                                                    float& effective_voxel_size,
                                                    ProgressCallback progress) {
    ReportProgress(progress, 1);
    PointCloud source;
    if (!open3d::t::io::ReadPointCloud(filename, source) || !source.HasPointPositions()) {
        throw std::runtime_error("File is neither a supported PLY mesh nor a PLY point cloud.");
    }
    const size_t source_count = static_cast<size_t>(source.GetPointPositions().GetLength());
    if (source_count > kMaximumInputVertices) {
        throw std::runtime_error("PLY point cloud exceeds the 2M point processing limit.");
    }
    input_vertices = static_cast<uint32_t>(source_count);

    ReportProgress(progress, 3);
    auto cloud = source.VoxelDownSample(requested_voxel_size);
    double voxel_size = requested_voxel_size;
    for (int attempt = 0;
         cloud.GetPointPositions().GetLength() > kMaximumPoints && attempt < 12;
         ++attempt) {
        voxel_size *= 1.5;
        cloud = source.VoxelDownSample(voxel_size);
    }
    if (cloud.GetPointPositions().GetLength() > kMaximumPoints) {
        throw std::runtime_error("Point cloud could not be reduced below 100,000 points.");
    }
    effective_voxel_size = static_cast<float>(voxel_size);
    if (cloud.GetPointPositions().GetLength() < 50) {
        throw std::runtime_error("Point cloud has fewer than 50 points after voxel cleanup.");
    }

    const auto neighbor_count = static_cast<size_t>(std::min<int>(
            outlier_neighbors, static_cast<int>(cloud.GetPointPositions().GetLength()) - 1));
    ReportProgress(progress, 4);
    auto filtered = cloud.RemoveStatisticalOutliers(neighbor_count, outlier_std_ratio);
    cloud = std::move(std::get<0>(filtered));
    if (cloud.GetPointPositions().GetLength() < 50) {
        throw std::runtime_error("Outlier removal left fewer than 50 scan points.");
    }

    const int normal_neighbors = static_cast<int>(std::min<int64_t>(
            30, cloud.GetPointPositions().GetLength() - 1));
    ReportProgress(progress, 5);
    cloud.EstimateNormals(normal_neighbors, voxel_size * 2.0);
    cloud.OrientNormalsConsistentTangentPlane(
            static_cast<size_t>(normal_neighbors));

    auto legacy_cloud = cloud.ToLegacy();
    ReportProgress(progress, 6);
    auto reconstruction = TriangleMesh::CreateFromPointCloudPoisson(
            legacy_cloud, static_cast<size_t>(poisson_depth), 0.0f, 1.1f,
            false, static_cast<int>(ProcessingThreads()));
    auto mesh = std::move(std::get<0>(reconstruction));
    auto densities = std::move(std::get<1>(reconstruction));
    if (!mesh || mesh->triangles_.empty()) {
        throw std::runtime_error("Open3D could not reconstruct a surface from this point cloud.");
    }
    if (poisson_density_quantile > 0.0 && !densities.empty()) {
        const size_t quantile_index = static_cast<size_t>(poisson_density_quantile * (densities.size() - 1));
        auto quantile = densities;
        std::nth_element(quantile.begin(), quantile.begin() + quantile_index, quantile.end());
        const double cutoff = quantile[quantile_index];
        std::vector<bool> remove_mask(densities.size());
        for (size_t i = 0; i < densities.size(); ++i) remove_mask[i] = densities[i] < cutoff;
        mesh->RemoveVerticesByMask(remove_mask);
        if (mesh->triangles_.empty()) {
            throw std::runtime_error("Poisson density cropping removed the entire reconstructed surface.");
        }
    }
    ReportProgress(progress, 7);
    if (mesh->triangles_.size() > static_cast<size_t>(target_triangles)) {
        auto simplified = mesh->SimplifyQuadricDecimation(
                target_triangles, std::numeric_limits<double>::max(), 1.0);
        if (!simplified) throw std::runtime_error("Open3D could not simplify the reconstructed mesh.");
        mesh = std::move(simplified);
    }
    CleanMesh(*mesh);
    return mesh;
}
}  // namespace

API const char* cadyx_scan_version() noexcept { return "0.20.0"; }

API void cadyx_scan_free(void* value) noexcept {
    delete[] static_cast<uint8_t*>(value);
}

API void* cadyx_scan_process(const char* filename,
                             int source_has_faces,
                             double voxel_size,
                             int target_triangles,
                             int outlier_neighbors,
                             double outlier_std_ratio,
                             int poisson_depth,
                             double poisson_density_quantile,
                             ProgressCallback progress,
                             size_t* output_size,
                             char* error,
                             size_t error_capacity) noexcept {
    if (output_size) *output_size = 0;
    if (!filename || !*filename || !output_size) {
        SetError(error, error_capacity, "A scan path and output buffer are required.");
        return nullptr;
    }
    if (!std::isfinite(voxel_size) || voxel_size <= 0 ||
        target_triangles < 100 || target_triangles > 100000 ||
        outlier_neighbors < 4 || outlier_neighbors > 128 ||
        !std::isfinite(outlier_std_ratio) || outlier_std_ratio < 0.1 || outlier_std_ratio > 5.0 ||
        poisson_depth < 5 || poisson_depth > 9 ||
        !std::isfinite(poisson_density_quantile) || poisson_density_quantile < 0.0 || poisson_density_quantile > 0.5) {
        SetError(error, error_capacity, "Scan processing parameters are outside supported limits.");
        return nullptr;
    }

    try {
        const unsigned int threads = ProcessingThreads();
        oneapi::tbb::global_control thread_limit(
                oneapi::tbb::global_control::max_allowed_parallelism, threads);
        uint32_t source_kind = 0;
        uint32_t input_vertices = 0;
        uint32_t input_triangles = 0;
        float effective_voxel_size = static_cast<float>(voxel_size);
        auto mesh = source_has_faces
                ? ReadAndSimplifyMesh(filename, voxel_size, target_triangles,
                                      input_vertices, input_triangles, progress)
                : nullptr;
        if (mesh) {
            source_kind = 1;
        } else {
            source_kind = 2;
            mesh = ReconstructPointCloud(filename, voxel_size, target_triangles,
                                         outlier_neighbors, outlier_std_ratio,
                                         poisson_depth, poisson_density_quantile, input_vertices,
                                         effective_voxel_size, progress);
        }
        ReportProgress(progress, 8);
        auto bytes = SerializeMesh(*mesh, source_kind, input_vertices,
                                   input_triangles, effective_voxel_size);
        auto* result = new uint8_t[bytes.size()];
        std::memcpy(result, bytes.data(), bytes.size());
        *output_size = bytes.size();
        return result;
    } catch (const std::exception& exception) {
        SetError(error, error_capacity, exception.what());
    } catch (...) {
        SetError(error, error_capacity, "Open3D scan processing failed.");
    }
    return nullptr;
}
