import numpy as np
import pytest

from worldsim.tiles import TERRAIN_PROFILES, TerrainType
from worldsim.world import DEFAULT_SIZE, World


def test_world_dimensions():
    world = World(seed=42)
    assert world.terrain.shape == (DEFAULT_SIZE, DEFAULT_SIZE)
    assert world.elevation.shape == (DEFAULT_SIZE, DEFAULT_SIZE)
    assert world.moisture.shape == (DEFAULT_SIZE, DEFAULT_SIZE)


def test_same_seed_identical_output():
    a = World(seed=12345)
    b = World(seed=12345)
    np.testing.assert_array_equal(a.terrain, b.terrain)
    np.testing.assert_array_equal(a.elevation, b.elevation)
    np.testing.assert_array_equal(a.moisture, b.moisture)


def test_different_seed_different_output():
    a = World(seed=1)
    b = World(seed=2)
    assert not np.array_equal(a.terrain, b.terrain)


def test_all_terrain_types_reachable():
    found = set()
    for seed in range(20):
        found.update(World(seed=seed).terrain.ravel().tolist())
        if len(found) == len(TerrainType):
            break
    assert found == {tt.value for tt in TerrainType}


def test_movement_cost_grid():
    world = World(seed=7)
    costs = world.movement_cost
    assert costs.shape == (DEFAULT_SIZE, DEFAULT_SIZE)
    for tt in TerrainType:
        mask = world.terrain == tt.value
        if mask.any():
            assert np.all(costs[mask] == TERRAIN_PROFILES[tt].movement_cost)


def test_terrain_breakdown_sums_to_total():
    world = World(seed=99)
    total = sum(world.terrain_breakdown().values())
    assert total == DEFAULT_SIZE * DEFAULT_SIZE


def test_resource_yield_positive():
    world = World(seed=3)
    yields = world.resource_yield()
    assert yields["food"] > 0
    assert yields["wood"] > 0
    assert yields["stone"] > 0


@pytest.mark.parametrize("size", [16, 64])
def test_custom_size(size):
    world = World(seed=11, size=size)
    assert world.terrain.shape == (size, size)
