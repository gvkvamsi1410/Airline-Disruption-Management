"""IropScenarioGenerator - programmatic what-if variants of the base scenario.

Subclass of ``dse_do_utils.scenariorunner.ScenarioGenerator``.  Kept minimal:
the demo only needs the "no-swap / manual" baseline, driven by the
``forbidSwaps`` parameter.
"""
from __future__ import annotations

from dataclasses import dataclass

from dse_do_utils.datamanager import Inputs
from dse_do_utils.scenariorunner import ScenarioConfig, ScenarioGenerator


@dataclass
class IropScenarioConfig(ScenarioConfig):
    forbid_swaps: bool = False


class IropScenarioGenerator(ScenarioGenerator):
    scenario_config: IropScenarioConfig

    def generate_scenario(self) -> Inputs:
        new_inputs = super().generate_scenario()  # handles the Parameter table

        if getattr(self.scenario_config, "forbid_swaps", False):
            params = new_inputs["Parameter"].copy()
            if (params["param"] == "forbidSwaps").any():
                params.loc[params["param"] == "forbidSwaps", "value"] = "True"
            else:
                params.loc[len(params)] = {"param": "forbidSwaps", "value": "True"}
            new_inputs["Parameter"] = params

        return new_inputs
