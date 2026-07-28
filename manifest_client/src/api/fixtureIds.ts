/**
 * Stable identifiers for the fixture world.
 * KEEP IN SYNC with scripts/generate-fixtures.py, which lays artifacts out at
 * public/fixtures/<project_id>/exports/<part_id>/model.* (the verified
 * exporter layout — CONTRACT.md §2).
 */
export const FIXTURE_PROJECT_ID = "11111111-1111-4111-8111-111111111111";
export const FIXTURE_PROJECT_NAME = "fixture-project";

/** CAD part with committed source + exports (model.stl + model.step). */
export const FIXTURE_CAD_PART_ID = "22222222-2222-4222-8222-222222222222";
export const FIXTURE_CAD_PART_NAME = "bracket";

/** Mesh part with exports (model.stl + model.glb; GLB ships authored material). */
export const FIXTURE_MESH_PART_ID = "33333333-3333-4333-8333-333333333333";
export const FIXTURE_MESH_PART_NAME = "spaceship";

/** CAD part whose STL is the 500k+-triangle performance fixture. */
export const FIXTURE_LARGE_PART_ID = "44444444-4444-4444-8444-444444444444";
export const FIXTURE_LARGE_PART_NAME = "dense-sphere";

/** Blank CAD part: no export history; first chat runs initial_cad_design. */
export const FIXTURE_BLANK_PART_ID = "55555555-5555-4555-8555-555555555555";
export const FIXTURE_BLANK_PART_NAME = "blank-part";
