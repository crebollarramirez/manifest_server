@dataclass(frozen=True)
class ModelParams:
    width_mm: float = 40.0
    depth_mm: float = 40.0
    height_mm: float = 40.0


# PART-START: starter_body
@mesh_part(
    id="starter_body",
    role="primary_mesh_form",
    library="blender_python",
    editable=True,
    object_names=("starter_body",),
    collection="structural_forms",
    parameters=("width_mm", "depth_mm", "height_mm"),
    depends_on=(),
    consumes_objects=(),
    produces_objects=("starter_body",),
    consumes_anchors=(),
    produces_anchors=(),
    protected_regions=(),
    validation_hooks=("object_exists", "mesh_has_faces", "finite_bounds"),
    export_targets=("stl", "glb"),
    search_keys=("starter body", "box", "base form"),
)
def build_starter_body(params: ModelParams) -> bpy.types.Object:
    """Build a parameterized starter box in the structural collection."""
    mesh = bpy.data.meshes.new("starter_body_mesh")
    obj = bpy.data.objects.new("starter_body", mesh)
    link_object(obj, "structural_forms")

    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=1.0)
        scale = Matrix.Diagonal(
            (
                mm(params.width_mm),
                mm(params.depth_mm),
                mm(params.height_mm),
                1.0,
            )
        )
        bmesh.ops.transform(bm, matrix=scale, verts=bm.verts)
        bm.to_mesh(mesh)
    finally:
        bm.free()

    mesh.update()
    obj["part_id"] = "starter_body"
    obj["semantic_role"] = "primary_mesh_form"
    obj["generator"] = "blender_python"
    obj["editable"] = True
    return obj
# PART-END: starter_body


def build_model(params: ModelParams) -> list[bpy.types.Object]:
    body = build_starter_body(params)
    return [body]
