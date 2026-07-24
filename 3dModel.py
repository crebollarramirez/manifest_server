from dataclasses import dataclass


@dataclass(frozen=True)
class ModelParams:
    wall_plate_width_mm: float = 63.5
    wall_plate_height_mm: float = 88.9
    wall_plate_thickness_mm: float = 8.0
    corner_radius_mm: float = 6.0

    screw_hole_diameter_mm: float = 4.5
    screw_head_counterbore_diameter_mm: float = 9.0
    screw_head_counterbore_depth_mm: float = 3.5
    mount_hole_edge_offset_mm: float = 8.0

    top_hook_width_mm: float = 28.0
    top_hook_depth_mm: float = 32.0
    top_hook_height_mm: float = 55.0
    top_hook_thickness_mm: float = 12.0
    top_hook_clearance_radius_mm: float = 32.0

    bottom_lip_width_mm: float = 42.0
    bottom_lip_depth_mm: float = 18.0
    bottom_lip_height_mm: float = 12.0


# PART-START: wall_plate
@cad_part(
    id="wall_plate",
    role="structural_base",
    library="cadquery",
    editable=True,
    protected_regions=("mounting_face", "mounting_holes"),
    parameters=("wall_plate_width_mm", "wall_plate_height_mm", "wall_plate_thickness_mm", "corner_radius_mm"),
    depends_on=(),
    consumes_tags=(),
    produces_tags=("wall_back_face", "wall_front_face", "wall_top_edge", "wall_bottom_edge"),
    search_keys=("rectangular bracket", "wall plate", "base plate"),
)
def build_wall_plate(params: ModelParams):
    """Create the rectangular wall-facing base plate. Depends only on the size parameters."""
    plate = (
        cq.Workplane("XY")
        .box(params.wall_plate_width_mm, params.wall_plate_height_mm, params.wall_plate_thickness_mm)
        .edges("|Z")
        .fillet(params.corner_radius_mm)
    )

    plate.faces("<Z").tag("wall_back_face")
    plate.faces(">Z").tag("wall_front_face")
    plate.edges(">Y").tag("wall_top_edge")
    plate.edges("<Y").tag("wall_bottom_edge")
    return plate
# PART-END: wall_plate


# PART-START: mount_holes
@cad_part(
    id="mount_holes",
    role="fastener_features",
    library="cadquery",
    editable=True,
    protected_regions=("mounting_face", "mounting_holes"),
    parameters=("screw_hole_diameter_mm", "screw_head_counterbore_diameter_mm", "screw_head_counterbore_depth_mm", "mount_hole_edge_offset_mm"),
    depends_on=("wall_plate",),
    consumes_tags=("wall_front_face",),
    produces_tags=(),
    search_keys=("four screws", "corner holes", "counterbore"),
)
def cut_mounting_holes(params: ModelParams, wall_plate):
    """Cut four corner screw holes through the wall plate. Depends on the wall plate geometry and its front face."""
    half_width = params.wall_plate_width_mm / 2.0
    half_height = params.wall_plate_height_mm / 2.0

    x_offset = half_width - params.mount_hole_edge_offset_mm
    y_offset = half_height - params.mount_hole_edge_offset_mm

    hole_points = [
        (x_offset, y_offset),
        (-x_offset, y_offset),
        (x_offset, -y_offset),
        (-x_offset, -y_offset),
    ]

    holes = (
        wall_plate.faces(">Z")
        .workplane()
        .pushPoints(hole_points)
        .cboreHole(
            params.screw_hole_diameter_mm,
            params.screw_head_counterbore_diameter_mm,
            params.screw_head_counterbore_depth_mm,
        )
    )
    return holes
# PART-END: mount_holes


# PART-START: hook_arm
@cad_part(
    id="hook_arm",
    role="load_bearing_arm",
    library="cadquery",
    editable=True,
    protected_regions=("headphone_contact_face",),
    parameters=("top_hook_width_mm", "top_hook_depth_mm", "top_hook_height_mm", "top_hook_thickness_mm", "top_hook_clearance_radius_mm"),
    depends_on=("wall_plate",),
    consumes_tags=(),
    produces_tags=(),
    search_keys=("headphone holder", "support arm", "hanger"),
)
def build_hook_arm(params: ModelParams, wall_plate):
    """Create the upper support arm that holds the headphones. Depends on the wall plate geometry."""
    hook_center_y = params.wall_plate_height_mm / 2.0 - params.top_hook_height_mm / 2.0
    plate_front_z = params.wall_plate_thickness_mm / 2.0
    arm_center_z = plate_front_z + params.top_hook_depth_mm / 2.0
    arm_fillet_mm = min(
        params.top_hook_clearance_radius_mm,
        params.top_hook_width_mm / 2.0 - 0.1,
        params.top_hook_thickness_mm / 2.0 - 0.1,
    )

    support_arm = (
        cq.Workplane("XY")
        .box(
            params.top_hook_width_mm,
            params.top_hook_thickness_mm,
            params.top_hook_depth_mm,
        )
        .edges("|Z")
        .fillet(arm_fillet_mm)
        .translate((0.0, hook_center_y, arm_center_z))
    )
    return wall_plate.union(support_arm)
# PART-END: hook_arm


# PART-START: bottom_lip
@cad_part(
    id="bottom_lip",
    role="anti_slip_retainer",
    library="cadquery",
    editable=True,
    protected_regions=("headphone_contact_face",),
    parameters=("bottom_lip_width_mm", "bottom_lip_depth_mm", "bottom_lip_height_mm"),
    depends_on=("hook_arm",),
    consumes_tags=(),
    produces_tags=(),
    search_keys=("support lip", "retainer", "stop"),
)
def build_bottom_lip(params: ModelParams, hook_arm):
    """Create the lower lip that keeps the headphones from sliding off. Depends on the hook arm geometry."""
    hook_center_y = params.wall_plate_height_mm / 2.0 - params.top_hook_height_mm / 2.0
    plate_front_z = params.wall_plate_thickness_mm / 2.0
    lip_center_y = hook_center_y + params.top_hook_thickness_mm / 2.0 + params.bottom_lip_height_mm / 2.0 - 1.0
    lip_center_z = plate_front_z + params.top_hook_depth_mm - params.bottom_lip_depth_mm / 2.0

    end_lip = (
        cq.Workplane("XY")
        .box(
            params.bottom_lip_width_mm,
            params.bottom_lip_height_mm,
            params.bottom_lip_depth_mm,
        )
        .translate((0.0, lip_center_y, lip_center_z))
    )
    return hook_arm.union(end_lip)
# PART-END: bottom_lip


# PART-START: edge_fillets
@cad_part(
    id="edge_fillets",
    role="finishing",
    library="cadquery",
    editable=True,
    protected_regions=(),
    parameters=("corner_radius_mm",),
    depends_on=("bottom_lip",),
    consumes_tags=(),
    produces_tags=(),
    search_keys=("fillet", "finish"),
)
def apply_edge_fillets(params: ModelParams, bottom_lip):
    """Apply final edge softening to exposed outer edges. Depends on the full assembled solid."""
    finish_radius_mm = max(0.4, min(0.8, params.corner_radius_mm * 0.1))
    finished = bottom_lip.edges("|Z").fillet(finish_radius_mm)
    return finished
# PART-END: edge_fillets


def _translate_model_to_origin(model):
    bbox = model.val().BoundingBox()
    return model.translate((-bbox.xmin, -bbox.ymin, -bbox.zmin))


def build_model(params: ModelParams):
    wall_plate = build_wall_plate(params)
    with_holes = cut_mounting_holes(params, wall_plate)
    hook_arm = build_hook_arm(params, with_holes)
    bottom_lip = build_bottom_lip(params, hook_arm)
    finished = apply_edge_fillets(params, bottom_lip)
    return _translate_model_to_origin(finished)
