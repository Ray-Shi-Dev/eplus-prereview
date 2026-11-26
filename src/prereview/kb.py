"""Knowledge base of EnergyPlus error and warning messages.

Each entry explains what a message means, whether a reviewer is likely to care, and what to do.
Entries are generic (no project data). Matching is by regular expression on the message text
and its continuation lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .errfile import ErrMessage


@dataclass
class KbEntry:
    id: str
    pattern: str
    title: str
    meaning: str
    fixes: List[str] = field(default_factory=list)
    status: Optional[str] = None  # FAIL / REVIEW / INFO override; None = by message level
    ignore: bool = False          # caused by prereview's own output requests
    stage: str = ""               # input | sizing | simulation | output
    _rx: Optional[re.Pattern] = None

    def matches(self, msg: ErrMessage) -> bool:
        if self._rx is None:
            self._rx = re.compile(self.pattern, re.I | re.S)
        return bool(self._rx.search(msg.full_text))


KB: List[KbEntry] = [
    # ---------------------------------------------------------------- input processing
    KbEntry("E-ENUM", r"Failed to match against any enum values",
            "Invalid choice in a field",
            "A field contains a value the IDD does not allow (typo, or a keyword renamed between versions).",
            ["Open the object named in the message and pick one of the allowed keywords.",
             "If the file was written for another EnergyPlus version, transition it with IDFVersionUpdater "
             "instead of editing keywords by hand."], stage="input"),
    KbEntry("E-FIELDS", r"(more fields than|maximum (number of )?fields|too many fields|Extensible field)",
            "Object has too many fields",
            "An object has more fields than its definition allows — usually a missing semicolon that merges two "
            "objects, or an object written for a different EnergyPlus version.",
            ["Check the object just before the line in the message for a missing ';'.",
             "Confirm the Version object matches the EnergyPlus executable."], stage="input"),
    KbEntry("E-BRANCH", r"(Branch|BranchList|Connector:Splitter|Connector:Mixer).*(not found|no match|mismatch)|"
            r"GetBranch(List|Data|Input)",
            "Plant or air loop branch is not connected",
            "A Branch, BranchList or connector refers to something missing (e.g. a hot-water branch removed from "
            "the BranchList but still listed in a splitter/mixer).",
            ["Make the BranchList, Connector:Splitter and Connector:Mixer lists agree with the Branch objects.",
             "Check that each branch's inlet/outlet nodes are unique and connected."], stage="input"),
    KbEntry("E-NODE", r"Node Connection Error|Check Node Connections|node.*(not connected|connected more than once|"
            r"is not found on any)|Duplicate Node",
            "HVAC node connection problem",
            "A node is used in the wrong place, used twice, or not connected to anything.",
            ["Follow the node names in the message through the air/plant loop and fix the inlet/outlet chain.",
             "The HVACTopology report in the HTML output shows how EnergyPlus connected the loops."],
            stage="input"),
    KbEntry("E-SUBSURF-AREA", r"(Surface Openings have too much area|subsurfaces? .*(area|larger).*base surface|"
            r"window.*not (inside|within) (its )?base surface)",
            "Windows or doors larger than their wall",
            "The sub-surfaces on a base surface add up to more than its area, or a window extends outside it "
            "(often overlapping glazing after a geometry edit).",
            ["Check the window/door vertices against the parent surface; remove duplicated or overlapping glazing.",
             "If the WWR was scaled, scale windows about their own centroid so they stay inside the wall."],
            stage="input"),
    KbEntry("E-WIN-MULT", r"Multiplier > 1\.0 for window|window multiplier.*daylight",
            "Window multiplier in a daylit zone",
            "A window with a multiplier greater than 1 is in a zone with daylighting controls (or the multiplier "
            "makes openings exceed the wall). Daylighting needs each window modeled explicitly.",
            ["Set the window multiplier to 1 and model each window as its own surface, or remove daylighting "
             "controls from the zone."], stage="input"),
    KbEntry("E-ZERO-AREA", r"(zero or negative (surface )?area|degenerate|collinear|less than 3 vertices|"
            r"distance between two vertices)",
            "Degenerate surface geometry",
            "A surface has zero or negative area or repeated/collinear vertices (often overlapped glazing or a "
            "collapsed polygon from geometry export).",
            ["Fix or delete the surface named in the message; re-export geometry with a vertex tolerance.",
             "Look for duplicated windows at the same location."], stage="input"),
    KbEntry("E-UPSIDE", r"upside down|Tilt angle.*(roof|floor)|Roof/Ceiling.*Tilt|Floor.*Tilt",
            "Surface facing the wrong way",
            "A floor or roof has its outward normal pointing the wrong way. EnergyPlus continues; usually minor, "
            "but a reversed exterior roof gets the wrong solar exposure.",
            ["Reverse the vertex order of the surface if it is an exterior roof, ground floor or exposed floor.",
             "Interior floors/ceilings flagged this way usually do not change results."], status="INFO",
            stage="input"),
    KbEntry("E-INTERZONE", r"InterZone.*(area|areas).*(do not match|mismatch|differ)|"
            r"Interzone surface.*does not match",
            "Interzone surface areas differ",
            "Two surfaces declared as each other's interzone partner have different areas. Usually minor; large "
            "differences shift heat transfer between zones.",
            ["Check the paired surfaces have matching geometry; regenerate the adjacency if the difference is large."],
            status="INFO", stage="input"),
    KbEntry("E-GROUND", r"Surfaces with interface to Ground found but no Ground Temperatures were input|"
            r"Ground Temperatures? .*(default|not input)",
            "Default ground temperatures used",
            "Ground-contact surfaces use EnergyPlus's default ground temperatures (18 °C every month).",
            ["Add Site:GroundTemperature:BuildingSurface values for the site (e.g. from a ground heat transfer "
             "preprocessor) and use the same values in proposed and baseline."], status="INFO", stage="input"),
    KbEntry("E-NOFLOOR", r"(No floor|has no floor|Zone.*floor area.*(zero|0\.0)|volume.*(not positive|zero|<=))",
            "Zone with no floor or volume",
            "A zone has no floor surfaces or a non-positive volume; its floor area may be missing from building "
            "totals and lighting/occupancy densities.",
            ["Check the zone's surfaces; enter zone floor area/volume explicitly if the geometry is intentionally "
             "simplified."], status="REVIEW", stage="input"),
    KbEntry("E-CONVEX", r"(non-convex|not convex|is not planar|non-planar)",
            "Non-convex or non-planar surface",
            "Shading/solar calculations assume convex, planar surfaces; EnergyPlus continues with an approximation.",
            ["Split the surface into convex pieces if it receives significant sun."], status="INFO", stage="input"),
    KbEntry("E-SCHED-LIMITS", r"ProcessScheduleInput:.*(Schedule Type Limits|ScheduleTypeLimits)|"
            r"Schedule Type Limits Name is empty",
            "Schedule without type limits",
            "A schedule has no ScheduleTypeLimits, so EnergyPlus cannot check its values are in range.",
            ["Assign a ScheduleTypeLimits (Fraction, Temperature, On/Off…) so out-of-range values are caught."],
            status="INFO", stage="input"),
    KbEntry("E-UNUSED", r"unused (schedules|constructions|materials|curves)|nominally unused",
            "Unused objects in the file",
            "Objects that nothing refers to. They do not change results, but reviewers reading the IDF may assume "
            "they are in use; leftovers from an earlier design option are a common source of confusion.",
            ["Remove unused objects before submission, or confirm none was meant to be referenced."],
            status="INFO", stage="input"),
    # ---------------------------------------------------------------- sizing
    KbEntry("W-ZERO-LOAD", r"Calculated design (cooling|heating) load for zone=.* is zero",
            "Zone with zero design load",
            "The sizing run found no design load for the zone (no gains, no exterior exposure, or not conditioned "
            "on that design day). Equipment for it may be sized at a minimum value.",
            ["Confirm the zone is meant to have no load on that design day; check its thermostat and internal-gain "
             "schedules for the design days (SummerDesignDay/WinterDesignDay day types)."], status="INFO",
            stage="sizing"),
    KbEntry("W-FLOW-PER-CAP", r"(Rated )?air volume flow rate per watt of rated total cooling capacity is out of range",
            "DX coil airflow per capacity outside the normal range",
            "The coil's airflow per unit capacity is outside 2.684e-5–6.713e-5 m³/s/W. EnergyPlus clamps the "
            "performance curves, so capacity and efficiency can be off when it is far outside.",
            ["Check the coil's rated airflow and capacity are sized together (both autosized or both from the "
             "same design data).",
             "Check the system design supply-air temperature: a high value drives airflow up relative to capacity."],
            status="INFO", stage="sizing"),
    KbEntry("W-AHRI", r"Standard Ratings is calculated .* not at the AHRI test condition",
            "Standard rating not at AHRI conditions",
            "Informational: the coil's curves do not cover the AHRI test point, so the reported SEER/EER is "
            "extrapolated. Simulation results are unaffected.",
            ["No action needed for compliance; do not quote the reported SEER/EER as the rated efficiency."],
            status="INFO", stage="sizing"),
    KbEntry("W-CURVE-RATED", r"curve values.*(output is not equal to 1\.0|not equal to 1\.0 \(\+ or - 10%\))|"
            r"Curve output at rated conditions",
            "Performance curve not normalised at rated conditions",
            "A coil performance curve does not return 1.0 at rated conditions, so the coil's capacity or efficiency "
            "at rated conditions differs from the rated input. Common with curves copied from other equipment.",
            ["Use curves normalised to 1.0 at the rated point (e.g. from the EnergyPlus datasets), or confirm the "
             "effective rated performance matches the equipment schedule."], status="INFO", stage="sizing"),
    KbEntry("W-PLANT-SIZING", r"For a plant sizing run, there must be at least",
            "Plant sizing requested without Sizing:Plant",
            "SimulationControl asks for plant sizing but there is no Sizing:Plant object, so the option is ignored.",
            ["Add Sizing:Plant for each plant loop, or set the plant sizing option to No."], status="INFO",
            stage="sizing"),
    KbEntry("W-DD-MISMATCH", r"Weather file location will be used rather than entered",
            "Site location differs from the weather file",
            "The Site:Location in the IDF is not the weather file's location. Simulation uses the weather file, but "
            "design days and site data may be for another place.",
            ["Import Site:Location and design days from the .ddy that matches the weather file."],
            status="REVIEW", stage="sizing"),
    # ---------------------------------------------------------------- simulation
    KbEntry("S-SURF-TEMP", r"(CalcHeatBalanceInsideSurf|CalcHeatBalanceOutsideSurf|Temperature out of bounds|"
            r"temperature out of (bounds|range))",
            "Surface temperature out of bounds",
            "A surface reached an unphysical temperature. Common causes: a schedule with wrong values or format "
            "(e.g. gains in W where a fraction was expected), a tiny zone with very large gains, or a very "
            "lightweight construction with no HVAC.",
            ["Check the schedules of internal gains and setpoints in the zone named (values and format).",
             "Check the surface's construction layers and the zone's HVAC connection."], stage="simulation"),
    KbEntry("S-PSYCH", r"Psy\w+|psychrometric|Relative Humidity out of range|Saturation temperature",
            "Psychrometric calculation out of range",
            "A node or zone reached impossible air conditions (temperature/humidity), usually from extreme supply "
            "conditions, missing humidity control or a coil running with almost no airflow.",
            ["Find the component named in the continuation lines; check its airflow, setpoints and controls."],
            status="REVIEW", stage="simulation"),
    KbEntry("S-WARMUP", r"CheckWarmupConvergence|did not converge after \d+ warmup days|Warmup.*(did not converge|not converged)",
            "Warm-up did not converge",
            "Zone temperatures/loads had not stabilised when warm-up ended, so the first days of each run period "
            "start from an unsettled state.",
            ["Increase 'Maximum Number of Warmup Days' in the Building object (e.g. 50).",
             "Check for zones with very high thermal mass or no HVAC."], status="REVIEW", stage="simulation"),
    KbEntry("S-HVAC-ITER", r"Maximum iterations .* exceeded for all HVAC loops",
            "HVAC iterations did not converge",
            "In some timesteps the HVAC solution did not converge. A few occurrences are normal; thousands point to "
            "unstable controls.",
            ["If frequent, check setpoint managers, controller convergence tolerances and loop flow limits."],
            status="INFO", stage="simulation"),
    KbEntry("S-COIL-CONTROL", r"(Coil control failed|Iteration limit exceeded in calculating .*part-load ratio|"
            r"part-load ratio out of range|Controller:WaterCoil.*(did not converge|exceeded))",
            "Coil control could not meet the load",
            "The unitary system or coil controller could not find an operating point; often the coil is at full "
            "capacity and still short of the load, or controls are fighting.",
            ["Compare the coil capacity with the zone/system load in those hours; check controller tolerances."],
            status="INFO", stage="simulation"),
    KbEntry("S-COIL-FREEZE", r"outlet air (dry-bulb )?temperature[^.]*(below|<)[^.]*(freez|2(\.0)? ?C)",
            "DX coil outlet air near freezing",
            "The DX coil's outlet air is below about 2 °C at full load, which means too little airflow for its "
            "capacity. Real equipment would ice; the simulated capacity is optimistic.",
            ["Check the coil's rated airflow against its capacity (about 2.7e-5–6.7e-5 m³/s/W)."],
            status="REVIEW", stage="simulation"),
    KbEntry("S-SETPOINT", r"Missing temperature setpoint|setpoint.*(not found|missing)|no setpoint",
            "Missing setpoint on a controlled node",
            "A component that needs a setpoint on its outlet node has none, so it may not run as intended.",
            ["Add a SetpointManager for the node named in the message."], status="REVIEW", stage="simulation"),
    KbEntry("S-PLANT-TEMP", r"(Plant|Loop).*temperature.*(exceed|above|below).*(limit|maximum|minimum)|"
            r"(chiller|boiler).*(temperature|flow).*(limit|out of range)",
            "Plant temperature outside limits",
            "A plant loop or plant component ran outside its temperature limits (often undersized equipment or "
            "conflicting setpoints).",
            ["Check plant equipment sizing and loop setpoints; compare with the loop's design temperatures."],
            status="REVIEW", stage="simulation"),
    KbEntry("S-DAYLIGHT", r"Fraction of zone or space controlled by the Daylighting reference points is <",
            "Daylighting controls cover part of the zone",
            "Informational: the daylighting control fractions add up to less than 1, so part of the zone's "
            "lighting is not dimmed.",
            ["No action needed unless the full zone is meant to be daylight-controlled."], status="INFO",
            stage="simulation"),
    KbEntry("S-DAMPER-REHEAT", r"Damper Heating Action = NORMAL, input for Maximum Flow Fraction During Reheat",
            "Reheat flow fraction ignored",
            "Informational: with NORMAL damper heating action the maximum flow fraction during reheat is not used.",
            [], status="INFO", stage="simulation"),
    KbEntry("S-WATER", r"(Use side nodes are specified; Peak Volumetric Use Flow Rate will not be used|"
            r"Target water temperature is less than the cold water temperature|Pump nominal power or motor "
            r"efficiency is set to)",
            "Service water heating input note",
            "Informational message about service water heating inputs.",
            ["Confirm service water heating inputs are the same in proposed and baseline where required."],
            status="INFO", stage="simulation"),
    # ---------------------------------------------------------------- outputs
    KbEntry("O-PREREVIEW", r"requested but not generated.*(ZONE GAS EQUIPMENT NATURALGAS RATE|ZONE OTHER EQUIPMENT|"
            r"VAV DAMPER POSITION|ZONE PEOPLE OCCUPANT COUNT|ZONE LIGHTS ELECTRICITY RATE|ZONE ELECTRIC EQUIPMENT|"
            r"SYSTEM NODE|FACILITY .* SETPOINT NOT MET|SCHEDULE VALUE)",
            "Output requests added by prereview",
            "Some of the hourly outputs prereview requests do not exist in this model (for example no VAV "
            "terminals). Harmless.", [], status="INFO", ignore=True, stage="output"),
    KbEntry("O-MONTHLY", r"Processing Monthly Tabular Reports: Variable names not valid",
            "Monthly report variables not available",
            "An Output:Table:Monthly report asks for variables that do not exist in this model. Harmless.",
            [], status="INFO", stage="output"),
    KbEntry("E-REF", r'(not found|was not found|invalid reference|Missing required property|cannot be found)',
            "Reference to an object that does not exist",
            "A field names an object (schedule, construction, node, branch, curve…) that is not defined, or the "
            "name is spelled differently.",
            ["Search the IDF for the exact name in the message (names are case-insensitive but spaces count).",
             "If an object was renamed or deleted, update every field that refers to it."], stage="input"),
    KbEntry("O-NOTGEN", r"requested but not generated",
            "Requested outputs not generated",
            "Some Output:Variable requests do not match anything in the model. Harmless for results.",
            ["Remove or correct the requests if a report depends on them."], status="INFO", stage="output"),
]


def lookup(msg: ErrMessage) -> Optional[KbEntry]:
    for entry in KB:
        if entry.matches(msg):
            return entry
    return None
