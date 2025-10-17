# Standard library
import logging
import math
import time
import sys
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Literal, TYPE_CHECKING, cast

# ETS2LA framework
from ETS2LA.Events import events
from ETS2LA.Plugin import Author, ETS2LAPlugin, PluginDescription
from ETS2LA.Utils.translator import _

# Modules
from Modules.Traffic.classes import Vehicle

# Local
sys.path.append("CataloguePlugins.AutomaticOvertake")
from settings import settings
from ui import SettingsPage

if TYPE_CHECKING:
    from Modules.SDKController.main import SCSController as Controller

Side = Literal["left", "right"]
SpeedEvent = Literal["increment_speed", "decrement_speed"]

INDICATOR_PULSE_S = 0.1
INDICATOR_COOLDOWN_S = 1.0
LANE_TOLERANCE_FACTOR = 0.75
LANE_CHANGE_STALL_BUFFER_S = 3.0
CLEARING_TIMEOUT_S = 30.0
SPEED_EVENT_HOLD_S = 0.25

logger = logging.getLogger(__name__)


class OvertakeState(Enum):
    IDLE = auto()
    MONITORING = auto()
    REQUESTING_OUT = auto()
    CHANGING_OUT = auto()
    CLEARING = auto()
    REQUESTING_RETURN = auto()
    RETURNING = auto()


class Plugin(ETS2LAPlugin):
    description = PluginDescription(
        name=_("Automatic Overtake"),
        version="1.0.1",
        description=_("Automatic Overtaking Assistant."),
        modules=["TruckSimAPI", "Traffic", "SDKController"],
        tags=["Base", "Automation", "Speed Control"],
        fps_cap=10,
    )

    author = Author(
        name="ReqT1m3out",
        url="https://github.com/reqt1m3out",
        icon="https://avatars.githubusercontent.com/u/63476357?v=4",
    )

    pages = [SettingsPage]

    def imports(self):
        global Controller
        from Modules.SDKController.main import SCSController as Controller

    def init(self):
        self.controller = cast("Controller", self.modules.SDKController.SCSController())
        self._initialize_runtime_state()
        self._refresh_side_preferences()
        self._set_phase(OvertakeState.IDLE, "Initialized")

    def _initialize_runtime_state(self):
        now = time.time()

        self._state = OvertakeState.IDLE
        self._state_since = now
        self._state_reason = "Boot"
        self._last_logged_state: Optional[Tuple[OvertakeState, str]] = None

        self._pass_side: Side = "left"
        self._requested_side: Optional[Side] = None
        self._original_side: Side = "right"

        self._lead_vehicle_id: Optional[int] = None
        self._lead_last_seen = 0.0
        self._overtaken_vehicle_id: Optional[int] = None

        self._pending_indicator_attr: Optional[str] = None
        self._pending_indicator_release = 0.0
        self._last_indicator_side: Optional[Side] = None
        self._last_indicator_time = 0.0

        self._observed_execution = False
        self._last_lane_status = "idle"

        self._forward_vector: Optional[Tuple[float, float]] = None
        self._right_vector: Optional[Tuple[float, float]] = None

        self._speed_boost_applied = 0
        self._speed_boost_target = 0
        self._active_speed_event: Optional[SpeedEvent] = None
        self._active_speed_event_started = 0.0

        self.state.reset()
        self.state.text = _("Idle")
        self.state.progress = -1

    def _refresh_side_preferences(self):
        self._pass_side = "left" if settings.preferred_side == "PassLeft" else "right"

    def _update_ui_state(self):
        label = self._state.name.replace("_", " ").title()
        if self._state_reason:
            self.state.text = f"{label} - {self._state_reason}"
        else:
            self.state.text = label
        self.state.progress = -1

    def _set_phase(
        self, new_state: OvertakeState, reason: str, *, log_level=logging.INFO
    ):
        if self._state == new_state and reason == self._state_reason:
            return

        self._state = new_state
        self._state_since = time.time()
        self._state_reason = reason

        if self._last_logged_state != (new_state, reason):
            logger.log(log_level, "%s -> %s", new_state.name, reason)
            self._last_logged_state = (new_state, reason)

        self._update_ui_state()

    def _set_reason(self, reason: str):
        if self._state_reason == reason:
            return

        self._state_reason = reason
        self._update_ui_state()

    def _reset_state(self, reason: str):
        self._set_phase(OvertakeState.IDLE, reason, log_level=logging.INFO)
        self._requested_side = None
        self._lead_vehicle_id = None
        self._overtaken_vehicle_id = None
        self._lead_last_seen = 0.0
        self._observed_execution = False

        self._remove_speed_boost()
        self._update_speed_adjustments(time.time())

    @events.on("takeover")
    def on_takeover(self, event_object, *args, **kwargs):
        if self._state != OvertakeState.IDLE:
            logger.warning(
                "Overtake interrupted by takeover (was in %s)", self._state.name
            )
            self._reset_state("Driver takeover")

    def _read_tag(self, name: str, default=None):
        try:
            value = getattr(self.tags, name, None)
            if value is None:
                return default

            merged = self.tags.merge(value)
            return default if merged is None else merged

        except (AttributeError, ValueError, TypeError) as error:
            logger.debug("Failed to read tag '%s': %s", name, error)
            return default
        except Exception:
            logger.exception("Unexpected error reading tag '%s'", name)
            return default

    def _update_indicator_pulse(self, now: float):
        if self._pending_indicator_attr and now >= self._pending_indicator_release:
            try:
                setattr(self.controller, self._pending_indicator_attr, False)
            except (AttributeError, TypeError) as error:
                logger.debug("Failed to release indicator: %s", error)
            except Exception as error:
                logger.error("Unexpected error releasing indicator: %s", error)
            finally:
                self._pending_indicator_attr = None
                self._pending_indicator_release = 0.0

    def _trigger_indicator(self, side: Side):
        attr = "lblinker" if side == "left" else "rblinker"
        opposite = "rblinker" if side == "left" else "lblinker"

        try:
            setattr(self.controller, opposite, False)
            setattr(self.controller, attr, True)
        except (AttributeError, TypeError) as error:
            logger.error("Failed to toggle indicators for %s side: %s", side, error)
            return
        except Exception as error:
            logger.exception("Unexpected error toggling indicators for %s side", side)
            return

        now = time.time()
        self._pending_indicator_attr = attr
        self._pending_indicator_release = now + INDICATOR_PULSE_S
        self._last_indicator_side = side
        self._last_indicator_time = now

    def _request_lane_change(self, side: Side):
        now = time.time()
        if (
            self._last_indicator_side == side
            and now - self._last_indicator_time < INDICATOR_COOLDOWN_S
        ):
            return

        self._trigger_indicator(side)

    def _orientation(
        self, api: Dict
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        rotation = api["truckPlacement"]["rotationX"] * 360
        if rotation < 0:
            rotation += 360

        yaw = math.radians(rotation)
        forward = (-math.sin(yaw), -math.cos(yaw))
        right = (math.cos(yaw), -math.sin(yaw))
        return forward, right

    def _project(self, api: Dict, x: float, z: float) -> Tuple[float, float]:
        if self._forward_vector and self._right_vector:
            forward, right = self._forward_vector, self._right_vector
        else:
            forward, right = self._orientation(api)

        truck_x = api["truckPlacement"]["coordinateX"]
        truck_z = api["truckPlacement"]["coordinateZ"]

        dx = x - truck_x
        dz = z - truck_z

        longitudinal = dx * forward[0] + dz * forward[1]
        lateral = dx * right[0] + dz * right[1]
        return longitudinal, lateral

    def _lane_is_clear(
        self,
        side: Side,
        api: Dict,
        traffic: List[Vehicle],
        *,
        front_clearance: float,
        rear_clearance: float,
        use_dynamic_rear: bool = True,
    ) -> bool:
        if not traffic:
            return True

        if use_dynamic_rear:
            speed_kph = api["truckFloat"]["speed"] * 3.6
            rear_clearance = self._calculate_safe_rear_clearance(speed_kph)

        lane_center = (
            settings.lane_width_m if side == "right" else -settings.lane_width_m
        )
        tolerance = settings.lane_width_m * LANE_TOLERANCE_FACTOR

        for vehicle in traffic:
            if vehicle.is_tmp or vehicle.is_trailer:
                continue

            longi, lateral = self._project(api, vehicle.position.x, vehicle.position.z)

            if longi > front_clearance or longi < -rear_clearance:
                continue

            if abs(lateral - lane_center) > tolerance:
                continue

            return False

        return True

    def _check_start_conditions(
        self,
        *,
        speed: float,
        speed_limit: float,
        lead_distance: Optional[float],
        lane_status: str,
        road_type: str,
        next_intersection_distance: Optional[float],
    ) -> Tuple[bool, str]:
        if lane_status != "idle":
            return False, "Lane change already active"

        if lead_distance is None or lead_distance <= 0:
            return False, "No vehicle in front"

        if lead_distance > settings.min_lead_distance_m:
            return False, "Lead vehicle too far"

        if speed < settings.min_speed_kph:
            return False, "Speed below threshold"

        if speed_limit > 0 and speed_limit - speed < settings.min_speed_delta_kph:
            return False, "Speed delta too small"

        if settings.require_highway and road_type != "highway":
            return False, "Road not marked as highway"

        if (
            isinstance(next_intersection_distance, (int, float))
            and next_intersection_distance > 0
            and next_intersection_distance < settings.intersection_buffer_m
        ):
            return False, "Intersection too close"

        return True, "Eligible"

    def _dependencies_ready(self, status: Dict) -> bool:
        if not status:
            return False
        if not status.get("Map"):
            return False
        if not status.get("AdaptiveCruiseControl"):
            return False
        return True

    def _get_opposite_side(self, side: Side) -> Side:
        return "right" if side == "left" else "left"

    def _calculate_safe_rear_clearance(self, speed_kph: float) -> float:
        speed_ms = speed_kph / 3.6
        dynamic_clearance = speed_ms * settings.rear_time_gap_s
        return max(settings.lane_clear_rear_m, dynamic_clearance)

    def _apply_speed_boost(self):
        boost_amount = int(settings.overtake_speed_boost_kph)
        if boost_amount <= 0:
            return

        if self._speed_boost_target >= boost_amount:
            return

        self._speed_boost_target = boost_amount
        logger.debug(
            "Scheduling speed boost: target +%d km/h (applied %+d km/h)",
            boost_amount,
            self._speed_boost_applied,
        )

    def _remove_speed_boost(self):
        if self._speed_boost_target == 0 and self._speed_boost_applied == 0:
            return

        self._speed_boost_target = 0
        logger.debug(
            "Scheduling speed boost removal (current %+d km/h)",
            self._speed_boost_applied,
        )

    def _finish_speed_adjustment(self, *, cancel: bool):
        if self._active_speed_event is None:
            return

        event_name = self._active_speed_event
        try:
            events.emit(event_name, self, False, queue=False)
        except Exception as error:
            logger.error("Failed to release %s event: %s", event_name, error)

        if not cancel:
            if event_name == "increment_speed":
                self._speed_boost_applied += 1
            else:
                self._speed_boost_applied = max(0, self._speed_boost_applied - 1)

        self._active_speed_event = None
        self._active_speed_event_started = 0.0

    def _update_speed_adjustments(self, now: float):
        hold_duration = SPEED_EVENT_HOLD_S

        while True:
            if self._active_speed_event is not None:
                event_direction = (
                    1 if self._active_speed_event == "increment_speed" else -1
                )
                pending_delta = self._speed_boost_target - self._speed_boost_applied
                desired_direction = (
                    1 if pending_delta > 0 else -1 if pending_delta < 0 else 0
                )

                if desired_direction == 0 or desired_direction != event_direction:
                    self._finish_speed_adjustment(cancel=True)
                    continue

                if now - self._active_speed_event_started < hold_duration:
                    return

                self._finish_speed_adjustment(cancel=False)
                continue

            pending_delta = self._speed_boost_target - self._speed_boost_applied
            if pending_delta == 0:
                return

            event_name: SpeedEvent = (
                "increment_speed" if pending_delta > 0 else "decrement_speed"
            )

            try:
                events.emit(event_name, self, True, queue=False)
            except Exception as error:
                logger.error("Failed to emit %s event: %s", event_name, error)
                return

            self._active_speed_event = event_name
            self._active_speed_event_started = now
            return

    def _is_overtaken_vehicle_clear(
        self, api: Dict, traffic: List[Vehicle], min_rear_distance: float
    ) -> bool:
        if self._overtaken_vehicle_id is None:
            return True

        overtaken_vehicle = None
        for vehicle in traffic:
            if vehicle.id == self._overtaken_vehicle_id:
                overtaken_vehicle = vehicle
                break

        if overtaken_vehicle is None:
            return True

        longi, _ = self._project(
            api, overtaken_vehicle.position.x, overtaken_vehicle.position.z
        )

        if longi < 0 and abs(longi) > min_rear_distance:
            return True

        return False

    def _publish_tags(
        self,
        *,
        is_active: bool,
        lead_distance: Optional[float],
        speed: Optional[float],
    ) -> None:
        self.tags.status = {"AutomaticOvertake": is_active}
        self.tags.overtake_state = {
            "state": self._state.name,
            "reason": self._state_reason,
            "targetSide": self._pass_side
            if self._requested_side is None
            else self._requested_side,
            "originalSide": self._original_side,
            "leadVehicleId": self._lead_vehicle_id,
            "overtakenVehicleId": self._overtaken_vehicle_id,
            "leadDistance": lead_distance,
            "speedKph": speed,
        }

    def run(self):
        now = time.time()

        self._refresh_side_preferences()
        self._update_indicator_pulse(now)
        self._update_speed_adjustments(now)

        if not settings.enabled:
            self._reset_state("Disabled")
            self.tags.status = {"AutomaticOvertake": False}
            self.tags.overtake_state = {"state": "Disabled", "reason": "Disabled"}
            self._last_lane_status = "idle"
            return

        status_tag = self._read_tag("status", {})
        if not isinstance(status_tag, dict):
            status_tag = {}

        lane_status = str(self._read_tag("lane_change_status", "idle") or "idle")
        road_type = str(self._read_tag("road_type", "none") or "none")
        next_intersection_distance = self._read_tag(
            "next_intersection_distance", None
        )
        lead_distance_raw = self._read_tag("vehicle_in_front_distance", None)
        vehicle_highlights = self._read_tag("vehicle_highlights", [])

        lead_distance = (
            float(lead_distance_raw)
            if isinstance(lead_distance_raw, (int, float))
            else None
        )

        if not self._dependencies_ready(status_tag):
            if self._state != OvertakeState.IDLE:
                self._reset_state("Waiting for Map/ACC")
            else:
                self._set_reason("Waiting for Map/ACC")

            self._publish_tags(
                is_active=False,
                lead_distance=lead_distance,
                speed=None,
            )
            self._last_lane_status = lane_status
            return

        api = self.modules.TruckSimAPI.run()
        if not isinstance(api, dict):
            self._reset_state("Telemetry unavailable")
            self._publish_tags(
                is_active=False,
                lead_distance=lead_distance,
                speed=None,
            )
            self._last_lane_status = lane_status
            return

        self._forward_vector, self._right_vector = self._orientation(api)

        speed = api["truckFloat"]["speed"] * 3.6
        speed_limit = api["truckFloat"]["speedLimit"] * 3.6
        if speed_limit == 0:
            speed_limit = speed + settings.min_speed_delta_kph

        previous_lead = self._lead_vehicle_id
        if (
            isinstance(vehicle_highlights, list)
            and vehicle_highlights
            and isinstance(vehicle_highlights[0], int)
        ):
            self._lead_vehicle_id = vehicle_highlights[0]
            self._lead_last_seen = now

        if (
            self._lead_vehicle_id is not None
            and self._lead_vehicle_id != previous_lead
            and self._state
            in (OvertakeState.MONITORING, OvertakeState.REQUESTING_OUT)
        ):
            logger.warning("Overtake aborted: lead vehicle changed")
            self._reset_state("Lead vehicle changed")
            self._publish_tags(
                is_active=False,
                lead_distance=lead_distance,
                speed=speed,
            )
            self._last_lane_status = lane_status
            return

        traffic: List[Vehicle] = []
        try:
            traffic_data = self.modules.Traffic.run()
            if isinstance(traffic_data, list):
                traffic = traffic_data
            else:
                logger.warning(
                    "Traffic module returned non-list data: %s", type(traffic_data)
                )
        except (AttributeError, ImportError) as error:
            logger.error("Traffic module not available: %s", error)
        except Exception as error:
            logger.exception("Failed to retrieve traffic data: %s", error)

        eligible, reason = self._check_start_conditions(
            speed=speed,
            speed_limit=speed_limit,
            lead_distance=lead_distance,
            lane_status=lane_status,
            road_type=road_type,
            next_intersection_distance=next_intersection_distance,
        )

        if self._state == OvertakeState.IDLE:
            if eligible:
                logger.warning(
                    "Overtake initiated: monitoring conditions (lead: %.1f m, speed: %.0f km/h, delta: %.0f km/h)",
                    lead_distance if lead_distance is not None else -1.0,
                    speed,
                    speed_limit - speed,
                )
                self._set_phase(OvertakeState.MONITORING, "Monitoring conditions")
            else:
                self._set_reason(reason)

        elif self._state == OvertakeState.MONITORING:
            if not eligible:
                logger.warning("Overtake aborted: %s", reason)
                self._reset_state(reason)
            elif now - self._state_since >= settings.hold_duration_s:
                lane_clear = self._lane_is_clear(
                    self._pass_side,
                    api,
                    traffic,
                    front_clearance=settings.lane_clear_front_m,
                    rear_clearance=settings.lane_clear_rear_m,
                )

                if lane_clear:
                    logger.warning(
                        "Starting overtake to %s (lead: %.1f m, speed: %.0f km/h)",
                        self._pass_side,
                        lead_distance if lead_distance is not None else -1.0,
                        speed,
                    )
                    self._overtaken_vehicle_id = self._lead_vehicle_id
                    self._original_side = self._get_opposite_side(self._pass_side)

                    self._apply_speed_boost()
                    self._update_speed_adjustments(now)

                    self._requested_side = self._pass_side
                    self._request_lane_change(self._pass_side)
                    self._observed_execution = False

                    self._set_phase(
                        OvertakeState.REQUESTING_OUT,
                        f"Requesting lane change to {self._pass_side}",
                    )
                else:
                    logger.warning("Overtake aborted: target lane occupied")
                    self._reset_state("Target lane occupied")
            else:
                self._set_reason("Verifying stability")

        elif self._state == OvertakeState.REQUESTING_OUT:
            if lane_status.startswith("executing"):
                self._observed_execution = True
                self._set_phase(OvertakeState.CHANGING_OUT, "Lane change started")
            elif now - self._state_since > settings.request_timeout_s:
                logger.warning("Lane change request timed out")
                self._reset_state("Lane change did not start")
            else:
                self._set_reason("Awaiting lane change start")
                self._request_lane_change(self._pass_side)

        elif self._state == OvertakeState.CHANGING_OUT:
            if lane_status.startswith("executing"):
                self._observed_execution = True
                self._set_reason(f"Executing lane change ({lane_status})")
            elif lane_status == "idle":
                if self._observed_execution:
                    self._set_phase(
                        OvertakeState.CLEARING, "Waiting for overtaken vehicle clearance"
                    )
                else:
                    logger.warning("Overtake aborted: lane change cancelled by Map")
                    self._reset_state("Lane change cancelled")
            elif (
                now - self._state_since
                > settings.request_timeout_s + LANE_CHANGE_STALL_BUFFER_S
            ):
                logger.warning("Lane change stalled during execution")
                self._reset_state("Lane change stalled")
            else:
                self._set_reason("Waiting for lane change to finish")

        elif self._state == OvertakeState.CLEARING:
            is_clear = self._is_overtaken_vehicle_clear(
                api, traffic, settings.return_clearance_m
            )

            if is_clear:
                return_lane_clear = self._lane_is_clear(
                    self._original_side,
                    api,
                    traffic,
                    front_clearance=settings.lane_clear_front_m,
                    rear_clearance=settings.lane_clear_rear_m,
                )

                if return_lane_clear:
                    self._requested_side = self._original_side
                    self._request_lane_change(self._original_side)
                    self._observed_execution = False
                    self._set_phase(
                        OvertakeState.REQUESTING_RETURN,
                        f"Requesting return to {self._original_side}",
                    )
                else:
                    self._set_reason("Waiting for original lane to clear")
            else:
                self._set_reason("Waiting for overtaken vehicle to clear")

            if now - self._state_since > CLEARING_TIMEOUT_S:
                logger.warning("Timeout waiting to return to original lane")
                self._reset_state("Return timeout")

        elif self._state == OvertakeState.REQUESTING_RETURN:
            if lane_status.startswith("executing"):
                self._observed_execution = True
                self._set_phase(
                    OvertakeState.RETURNING, "Returning to original lane"
                )
            elif now - self._state_since > settings.request_timeout_s:
                logger.warning("Return lane change request timed out")
                self._reset_state("Return request timeout")
            else:
                self._set_reason("Awaiting return lane change start")
                self._request_lane_change(self._original_side)

        elif self._state == OvertakeState.RETURNING:
            if lane_status.startswith("executing"):
                self._observed_execution = True
                self._set_reason(f"Executing return ({lane_status})")
            elif lane_status == "idle":
                if self._observed_execution:
                    logger.warning("Overtake complete")
                    self._remove_speed_boost()
                    self._reset_state("Overtake complete")
                else:
                    logger.warning("Return lane change cancelled by Map")
                    self._reset_state("Return cancelled")
            elif (
                now - self._state_since
                > settings.request_timeout_s + LANE_CHANGE_STALL_BUFFER_S
            ):
                logger.warning("Return lane change stalled")
                self._reset_state("Return stalled")
            else:
                self._set_reason("Waiting for return to finish")

        is_actively_overtaking = self._state in (
            OvertakeState.MONITORING,
            OvertakeState.REQUESTING_OUT,
            OvertakeState.CHANGING_OUT,
            OvertakeState.CLEARING,
            OvertakeState.REQUESTING_RETURN,
            OvertakeState.RETURNING,
        )

        self._publish_tags(
            is_active=is_actively_overtaking,
            lead_distance=lead_distance,
            speed=speed,
        )

        self._last_lane_status = lane_status
