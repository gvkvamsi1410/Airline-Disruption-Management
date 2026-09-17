"""Pandera schemas for the IROP scenario tables.

These document the contract of every input/output table and are used by
``IropDataManager.validate_inputs()`` to fail fast on a malformed scenario
workbook.  Times in the workbook are written as ``HH:MM`` strings for
readability and parsed to integer minutes-from-midnight by the DataManager;
the ``*_min`` columns below are the parsed, model-facing form.
"""
from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series


# --------------------------------------------------------------------------
# Input tables (as they appear in the Excel workbook)
# --------------------------------------------------------------------------
class AirportSchema(pa.DataFrameModel):
    airport: Series[str] = pa.Field(unique=True, description="Airport code, e.g. ORD.")

    class Config:
        name = "Airport"
        strict = False


class LegDurationSchema(pa.DataFrameModel):
    origin: Series[str] = pa.Field(description="Departure airport code.")
    dest: Series[str] = pa.Field(description="Arrival airport code.")
    duration_min: Series[int] = pa.Field(ge=1, description="Block time in minutes.")

    class Config:
        name = "LegDuration"
        strict = False


class TailSchema(pa.DataFrameModel):
    tail: Series[str] = pa.Field(unique=True, description="Aircraft tail id.")
    subtype: Series[str] = pa.Field(description="Fleet subtype; only matching flights are eligible.")
    start_location: Series[str] = pa.Field(description="Airport the tail begins the day at.")

    class Config:
        name = "Tail"
        strict = False


class FlightSchema(pa.DataFrameModel):
    flight: Series[str] = pa.Field(unique=True, description="Flight id.")
    origin: Series[str] = pa.Field(description="Departure airport.")
    dest: Series[str] = pa.Field(description="Arrival airport.")
    sched_dep: Series[str] = pa.Field(description="Scheduled departure, HH:MM.")
    subtype_required: Series[str] = pa.Field(description="Fleet subtype required to operate the flight.")
    orig_tail: Series[str] = pa.Field(description="Tail scheduled to operate the flight in the baseline plan.")

    class Config:
        name = "Flight"
        strict = False


class DisruptionSchema(pa.DataFrameModel):
    tail: Series[str] = pa.Field(description="Grounded tail.")
    unavailable_from: Series[str] = pa.Field(description="Start of the grounding window, HH:MM.")
    unavailable_until: Series[str] = pa.Field(description="End of the grounding window, HH:MM.")

    class Config:
        name = "Disruption"
        strict = False


class ParameterSchema(pa.DataFrameModel):
    param: Series[str] = pa.Field(unique=True)
    value: Series[object] = pa.Field()

    class Config:
        name = "Parameter"
        strict = False


# --------------------------------------------------------------------------
# Output tables
# --------------------------------------------------------------------------
class FlightRecoveryOutputSchema(pa.DataFrameModel):
    flight: Series[str] = pa.Field(unique=True)
    origin: Series[str]
    dest: Series[str]
    sched_dep_min: Series[int]
    actual_dep_min: Series[int] = pa.Field(nullable=True)
    delay_min: Series[int] = pa.Field(ge=0)
    orig_tail: Series[str]
    assigned_tail: Series[str] = pa.Field(nullable=True)
    swapped: Series[bool]
    cancelled: Series[bool]

    class Config:
        name = "FlightRecoveryOutput"
        strict = False


INPUT_SCHEMAS = {
    "Airport": AirportSchema,
    "LegDuration": LegDurationSchema,
    "Tail": TailSchema,
    "Flight": FlightSchema,
    "Disruption": DisruptionSchema,
    "Parameter": ParameterSchema,
}
