"""Measured reconstruction references from a mesh; no source geometry edits."""
from __future__ import annotations

import math
import random
from typing import Any

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry
from .scan_reverse_alignment import _jacobi_eigenvectors
from .scan_reverse_common import _cross, _dot, _unit
from .scan_reverse_sections import _intersections_for_triangle, _rdp, _simplify_closed


def normalize_recognition_parameters(parameters: Any) -> dict[str, Any]:
    defaults = {"distance_tolerance": .35, "normal_angle_degrees": 15.,
                "min_plane_area": 40., "max_planes": 12, "min_contour_length": 12.}
    if not isinstance(parameters, dict) or set(parameters) - defaults.keys():
        raise ValidationError("Unknown mesh recognition parameters.")
    result = {**defaults, **parameters}
    for key in ("distance_tolerance", "min_plane_area", "min_contour_length"):
        if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] <= 0:
            raise ValidationError("Recognition tolerances and minimum sizes must be positive finite source coordinates.")
    if type(result["max_planes"]) is not int or not 1 <= result["max_planes"] <= 24:
        raise ValidationError("max_planes must be an integer from 1 to 24.")
    if type(result["normal_angle_degrees"]) not in (int, float) or not 1 <= result["normal_angle_degrees"] <= 30:
        raise ValidationError("normal_angle_degrees must be between 1 and 30.")
    return result


def _pca(points, weights=None):
    weights = weights or [1.] * len(points)
    total = sum(weights)
    center = [sum(p[a]*w for p, w in zip(points, weights))/total for a in range(3)]
    covariance = [[sum(w*(p[a]-center[a])*(p[b]-center[b]) for p, w in zip(points, weights))/total
                   for b in range(3)] for a in range(3)]
    values, axes = _jacobi_eigenvectors(covariance)
    return center, axes, values


def _frame(origin, normal, x_axis):
    n = _unit(normal, "reference normal")
    x = _unit([x_axis[a]-_dot(x_axis,n)*n[a] for a in range(3)], "reference X")
    return {"origin": list(origin), "normal": n, "x_axis": x}


def _local(point, frame):
    delta = [point[a]-frame["origin"][a] for a in range(3)]
    y = _cross(frame["normal"], frame["x_axis"])
    return [_dot(delta, frame["x_axis"]), _dot(delta,y), _dot(delta,frame["normal"])]


def _world(point, frame):
    y = _cross(frame["normal"],frame["x_axis"])
    return [frame["origin"][a]+point[0]*frame["x_axis"][a]+point[1]*y[a]+point[2]*frame["normal"][a] for a in range(3)]


def _paths(positions, triangles, offset, tolerance):
    edges, points = set(), {}
    epsilon = tolerance*.1
    for i in range(0, len(triangles), 3):
        face = [positions[v] for v in triangles[i:i+3]]
        if offset < min(v[2] for v in face)-epsilon or offset > max(v[2] for v in face)+epsilon:
            continue
        hits = _intersections_for_triangle(face, 2, offset, epsilon)
        if len(hits) != 2 or math.dist(*hits) <= epsilon:
            continue
        keys = [tuple(round(v/tolerance) for v in point[:2]) for point in hits]
        if keys[0] == keys[1]:
            continue
        for key, point in zip(keys,hits):
            points.setdefault(key,point[:2])
        edges.add(tuple(sorted(keys)))
    adjacent = {}
    for a,b in sorted(edges):
        adjacent.setdefault(a,set()).add(b)
        adjacent.setdefault(b,set()).add(a)
    remaining, paths, branched = set(adjacent), [], 0
    while remaining:
        stack, component = [min(remaining)], set()
        while stack:
            v = stack.pop()
            if v not in component:
                component.add(v)
                stack.extend(adjacent[v]-component)
        remaining -= component
        if any(len(adjacent[v]) > 2 for v in component):
            branched += 1
            continue
        ends = sorted(v for v in component if len(adjacent[v]) == 1)
        seed = ends[0] if ends else min(component)
        chain, previous, current = [], None, seed
        while True:
            chain.append(points[current])
            options = sorted(adjacent[current]-({previous} if previous else set()))
            if not options or options[0] == seed:
                break
            previous,current = current,options[0]
        closed = not ends
        if len(chain) >= 3:
            paths.append((chain,closed))
    return paths, branched


def _circle3(a,b,c):
    bx,by,cx,cy = b[0]-a[0],b[1]-a[1],c[0]-a[0],c[1]-a[1]
    determinant = 2*(bx*cy-by*cx)
    if abs(determinant) < 1e-10:
        return None
    u,v = bx*bx+by*by,cx*cx+cy*cy
    x,y = (cy*u-by*v)/determinant,(bx*v-cx*u)/determinant
    return (a[0]+x,a[1]+y,math.hypot(x,y))


def _circles(points, tolerance, rng):
    sample = points[::max(1,len(points)//500)]
    if len(sample) < 12:
        return []
    candidates = []
    for _ in range(160):
        fit = _circle3(*rng.sample(sample,3))
        if fit is None or fit[2] < tolerance*3:
            continue
        inliers = [p for p in sample if abs(math.hypot(p[0]-fit[0],p[1]-fit[1])-fit[2]) <= tolerance]
        if len(inliers) < max(12,len(sample)*.15):
            continue
        angles = sorted(math.atan2(p[1]-fit[1],p[0]-fit[0])%(2*math.pi) for p in inliers)
        coverage = 2*math.pi-max(b-a for a,b in zip(angles,angles[1:]+[angles[0]+2*math.pi]))
        bins = len({int(a/(2*math.pi)*36) for a in angles})
        residuals = [abs(math.hypot(p[0]-fit[0],p[1]-fit[1])-fit[2]) for p in inliers]
        if coverage < math.pi/3 or bins < 6:
            continue
        candidates.append({"center": list(fit[:2]), "radius": fit[2],
                           "rms": math.sqrt(sum(v*v for v in residuals)/len(residuals)),
                           "max_error": max(residuals), "coverage_degrees": math.degrees(coverage),
                           "support_count": len(inliers), "sample_count": len(sample)})
    candidates.sort(key=lambda c:(-c["support_count"],c["rms"]))
    result = []
    for fit in candidates:
        if any(math.dist(fit["center"],c["center"]) < tolerance*4 and abs(fit["radius"]-c["radius"]) < tolerance*4 for c in result):
            continue
        result.append(fit)
        if len(result) == 3:
            break
    return result



def _simple_cycles(chain):
    stack, positions, cycles = [],{},[]
    for vertex in chain+[chain[0]]:
        if vertex in positions:
            index = positions[vertex]
            if len(stack)-index >= 3:
                cycles.append(stack[index:])
            for old in stack[index+1:]:
                positions.pop(old,None)
            stack = stack[:index+1]
        else:
            positions[vertex] = len(stack)
            stack.append(vertex)
    return cycles


def _projection(vertices, triangles, frame, tolerance, minimum_length):
    local = [_local(v,frame) for v in vertices]
    lo = [min(v[a] for v in local) for a in range(2)]
    hi = [max(v[a] for v in local) for a in range(2)]
    if (hi[0]-lo[0])*(hi[1]-lo[1])/tolerance**2 > 2000000:
        raise ValidationError("Projection exceeds its 2M cell budget; increase distance_tolerance.",code="RESOURCE_LIMIT")
    occupied = set()
    for i in range(0,len(triangles),3):
        a,b,c = [((local[v][0]-lo[0])/tolerance,(local[v][1]-lo[1])/tolerance) for v in triangles[i:i+3]]
        cross = (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        if abs(cross)<1e-12:
            continue
        for x in range(math.floor(min(a[0],b[0],c[0])),math.ceil(max(a[0],b[0],c[0]))):
            for y in range(math.floor(min(a[1],b[1],c[1])),math.ceil(max(a[1],b[1],c[1]))):
                if (x,y) in occupied:
                    continue
                px,py = x+.5,y+.5
                w0 = ((b[0]-px)*(c[1]-py)-(b[1]-py)*(c[0]-px))/cross
                w1 = ((c[0]-px)*(a[1]-py)-(c[1]-py)*(a[0]-px))/cross
                if w0>=-1e-9 and w1>=-1e-9 and w0+w1<=1+1e-9:
                    occupied.add((x,y))
    edges = set()
    for x,y in occupied:
        if (x,y-1) not in occupied: edges.add(((x,y),(x+1,y)))
        if (x+1,y) not in occupied: edges.add(((x+1,y),(x+1,y+1)))
        if (x,y+1) not in occupied: edges.add(((x+1,y+1),(x,y+1)))
        if (x-1,y) not in occupied: edges.add(((x,y+1),(x,y)))
    outgoing = {}
    for a,b in edges:
        outgoing.setdefault(a,set()).add(b)
    paths = []
    while edges:
        start,nxt = min(edges)
        chain,current,previous = [start],nxt,start
        edges.remove((start,nxt))
        while current != start:
            chain.append(current)
            options = [v for v in outgoing.get(current,()) if (current,v) in edges]
            if not options:
                break
            dx,dy = current[0]-previous[0],current[1]-previous[1]
            following = max(sorted(options),key=lambda v:math.atan2(dx*(v[1]-current[1])-dy*(v[0]-current[0]),
                                                                    dx*(v[0]-current[0])+dy*(v[1]-current[1])))
            edges.remove((current,following))
            previous,current = current,following
        if current != start or len(chain)<4 or len(chain)*tolerance<minimum_length:
            continue
        for cycle in _simple_cycles(chain):
            points = [(lo[0]+x*tolerance,lo[1]+y*tolerance) for x,y in cycle]
            simplified = _simplify_closed(points,tolerance*.5)
            area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(simplified,simplified[1:]+simplified[:1]))/2
            if area>0:
                paths.append({"frame":frame,"points":simplified,"closed":True,"area":area,
                              "role":"projection","grid_spacing":tolerance,
                              "simplify_tolerance":tolerance*.5,"fit_points":points})
    return sorted(paths,key=lambda p:-p["area"])[:1]


def _refined_circle(points):
    if len(points)<3:
        return None
    mx,my = (sum(p[a] for p in points)/len(points) for a in range(2))
    rows = [(p[0]-mx,p[1]-my,1.) for p in points]
    matrix = [[sum(r[a]*r[b] for r in rows) for b in range(3)]+
              [sum(r[a]*(r[0]*r[0]+r[1]*r[1]) for r in rows)] for a in range(3)]
    for col in range(3):
        pivot = max(range(col,3),key=lambda row:abs(matrix[row][col]))
        matrix[col],matrix[pivot] = matrix[pivot],matrix[col]
        if abs(matrix[col][col])<1e-12:
            return None
        divisor = matrix[col][col]
        matrix[col] = [v/divisor for v in matrix[col]]
        for row in range(3):
            if row!=col:
                factor = matrix[row][col]
                matrix[row] = [a-factor*b for a,b in zip(matrix[row],matrix[col])]
    x,y = matrix[0][3]/2,matrix[1][3]/2
    radius2 = matrix[2][3]+x*x+y*y
    return (mx+x,my+y,math.sqrt(radius2)) if radius2>0 else None


def _strict_circles(points,tolerance,rng):
    candidates = _circles(points,tolerance,rng)
    result = []
    for candidate in candidates:
        fit = (*candidate["center"],candidate["radius"])
        threshold = min(tolerance,fit[2]*.025)
        for _ in range(3):
            support = [p for p in points if abs(math.hypot(p[0]-fit[0],p[1]-fit[1])-fit[2])<=threshold]
            refined = _refined_circle(support)
            if refined is None: break
            fit = refined
        support = [p for p in points if abs(math.hypot(p[0]-fit[0],p[1]-fit[1])-fit[2])<=threshold]
        if len(support)<12: continue
        angles = sorted(math.atan2(p[1]-fit[1],p[0]-fit[0])%(2*math.pi) for p in support)
        coverage = 2*math.pi-max(b-a for a,b in zip(angles,angles[1:]+[angles[0]+2*math.pi]))
        gaps = sorted(b-a for a,b in zip(angles,angles[1:]+[angles[0]+2*math.pi]))
        if coverage<math.pi/2 or (len(gaps)>1 and gaps[-2]>math.radians(20)): continue
        halves = [_refined_circle(support[::2]),_refined_circle(support[1::2])]
        if any(f is None or abs(f[2]-fit[2])>threshold*2 or math.dist(f[:2],fit[:2])>threshold*2 for f in halves): continue
        residuals = [abs(math.hypot(p[0]-fit[0],p[1]-fit[1])-fit[2]) for p in support]
        result.append({"center":list(fit[:2]),"radius":fit[2],"rms":math.sqrt(sum(v*v for v in residuals)/len(residuals)),
                       "max_error":max(residuals),"coverage_degrees":math.degrees(coverage),"support_count":len(support),
                       "sample_count":len(points),"status":"candidate","radius_repeatability":max(abs(h[2]-fit[2]) for h in halves)})
    return sorted(result,key=lambda c:(-c["support_count"],c["rms"]))[:1]


def recognize_mesh_geometry(geometry: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    p = normalize_recognition_parameters(parameters)
    if geometry.get("kind") != "triangle_mesh" or geometry["triangle_count"] > 200000:
        raise ValidationError("Recognition requires a triangle mesh of at most 200000 triangles.")
    mesh = tessellate_mesh_geometry(geometry)
    vertices = [mesh["positions"][i:i+3] for i in range(0,len(mesh["positions"]),3)]
    triangles = mesh["triangles"]
    rng = random.Random(0)
    center,axes,_ = _pca(vertices[::max(1,len(vertices)//10000)])
    base = _frame(center,axes[2],axes[0])
    faces = []
    for i in range(0,len(triangles),3):
        pts = [vertices[v] for v in triangles[i:i+3]]
        cross = _cross([pts[1][a]-pts[0][a] for a in range(3)],[pts[2][a]-pts[0][a] for a in range(3)])
        area = math.sqrt(_dot(cross,cross))*.5
        if area > 1e-12:
            faces.append((i//3,[sum(v[a] for v in pts)/3 for a in range(3)],[v/(2*area) for v in cross],area))
    active = list(range(len(faces)))
    planes = []
    cosine = math.cos(math.radians(p["normal_angle_degrees"]))
    tol = p["distance_tolerance"]
    for _ in range(p["max_planes"]):
        if not active:
            break
        sample = active[::max(1,len(active)//2500)]
        seeds = rng.sample(sample,min(80,len(sample)))
        def support(normal,origin,indices):
            return [i for i in indices if abs(_dot(faces[i][2],normal)) >= cosine
                    and abs(_dot([faces[i][1][a]-origin[a] for a in range(3)],normal)) <= tol]
        best = max((support(faces[s][2],faces[s][1],sample) for s in seeds),
                   key=lambda group:sum(faces[i][3] for i in group))
        if not best:
            break
        fit_points = [vertices[v] for i in best for v in triangles[faces[i][0]*3:faces[i][0]*3+3]]
        fit_weights = [faces[i][3]/3 for i in best for _ in range(3)]
        origin,basis,_ = _pca(fit_points,fit_weights)
        normal = basis[2]
        selected = support(normal,origin,active)
        selected = [i for i in selected if max(abs(_dot([vertices[v][a]-origin[a] for a in range(3)],normal))
                    for v in triangles[faces[i][0]*3:faces[i][0]*3+3]) <= tol]
        area = sum(faces[i][3] for i in selected)
        if area < p["min_plane_area"]:
            break
        frame = _frame(origin,normal,basis[0])
        local = [_local(vertices[v],frame) for i in selected for v in triangles[faces[i][0]*3:faces[i][0]*3+3]]
        residuals = [abs(v[2]) for v in local]
        bounds = {"min":[min(v[a] for v in local) for a in range(2)],
                  "max":[max(v[a] for v in local) for a in range(2)]}
        planes.append({"frame":frame,"bounds":bounds,"area":area,"support_faces":[faces[i][0] for i in selected],
                       "rms":math.sqrt(sum(v*v for v in residuals)/len(residuals)),"max_error":max(residuals)})
        excluded = set(selected)
        active = [i for i in active if i not in excluded]
    sections, circles, rejected = [], [], 0
    for axis in range(3):
        frame = _frame(center,axes[axis],axes[(axis+1)%3])
        local = [_local(v,frame) for v in vertices]
        lo,hi = min(v[2] for v in local),max(v[2] for v in local)
        for fraction in (.25,.5,.75):
            offset = lo+(hi-lo)*fraction
            paths,branches = _paths(local,triangles,offset,tol*.3)
            rejected += branches
            section_frame = {**frame,"origin":_world([0,0,offset],frame)}
            for path,closed in paths:
                length = sum(math.dist(a,b) for a,b in zip(path,path[1:]+([path[0]] if closed else [])))
                if length < p["min_contour_length"]:
                    continue
                simplified = _simplify_closed(path,tol*.5) if closed else _rdp(path,tol*.5)
                sections.append({"frame":section_frame,"points":simplified,"closed":closed,
                                 "axis":axis,"offset":offset,"length":length,"simplify_tolerance":tol*.5,"join_tolerance":tol*.3})
                for fit in _strict_circles(path,tol,rng):
                    fit["frame"] = section_frame
                    fit["fit_source"] = "section"
                    circles.append(fit)
    projections = _projection(vertices,triangles,base,tol,p["min_contour_length"])
    for projection in projections:
        for fit in _strict_circles(projection.pop("fit_points"),tol,rng):
            fit["frame"],fit["fit_source"] = base,"projection"
            circles.append(fit)
    # Repeated circular section supports are cylinder candidates, not a fitted B-rep.
    cylinders = []
    for i,a in enumerate(circles):
        for b in circles[i+1:]:
            if a["fit_source"] != "section" or b["fit_source"] != "section":
                continue
            na,nb = a["frame"]["normal"],b["frame"]["normal"]
            ca,cb = _world([*a["center"],0],a["frame"]),_world([*b["center"],0],b["frame"])
            delta = [cb[k]-ca[k] for k in range(3)]
            height = abs(_dot(delta,na))
            off_axis = math.sqrt(max(0,_dot(delta,delta)-height*height))
            if abs(_dot(na,nb)) > .999 and height > tol*3 and off_axis < tol*2 and abs(a["radius"]-b["radius"]) < tol*2:
                cylinders.append({"origin":ca,"axis":na if _dot(delta,na)>=0 else [-v for v in na],"radius":(a["radius"]+b["radius"])/2,
                                  "supported_length":height,"status":"candidate","max_section_error":max(a["max_error"],b["max_error"])})
    unique = []
    for candidate in sorted(cylinders,key=lambda c:-c["supported_length"]):
        if any(abs(_dot(candidate["axis"],other["axis"]))>.999
               and abs(candidate["radius"]-other["radius"])<tol*2
               and math.sqrt(max(0,sum((candidate["origin"][k]-other["origin"][k])**2 for k in range(3))-
                                     _dot([candidate["origin"][k]-other["origin"][k] for k in range(3)],other["axis"])**2))<tol*2 for other in unique):
            continue
        unique.append(candidate)
    cylinders = unique
    return {"parameters":p,"principal_frame":base,"planes":planes,"sections":sections,"circles":circles,
            "cylinder_candidates":cylinders,"projections":projections,"branched_sections_rejected":rejected,
            "source_geometry_sha256":geometry["sha256"],"scale_status":"unverified",
            "readiness":"references_for_reconstruction",
            "unresolved":["Физический масштаб и номинальные размеры требуют подтверждения.",
                          "Открытые контуры необходимо восстановить перед построением тела.",
                          "Измеренные дуги не подтверждают полные окружности и цилиндры без проверки.",
                          "Самопересечения и производственная точность не проверены.",
                          "Проекция не является сечением; тонкие элементы требуют проверки при выбранном шаге."]}
