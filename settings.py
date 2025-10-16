from ETS2LA.Settings import ETS2LASettings
from typing import Literal


class Settings(ETS2LASettings):
    enabled: bool = True
    preferred_side: Literal["PassLeft", "PassRight"] = "PassLeft"
    min_speed_kph: float = 45.0
    min_lead_distance_m: float = 40.0
    min_speed_delta_kph: float = 12.0
    hold_duration_s: float = 2.0
    lane_clear_front_m: float = 55.0
    lane_clear_rear_m: float = 20.0
    rear_time_gap_s: float = 2.5
    return_clearance_m: float = 30.0
    intersection_buffer_m: float = 150.0
    request_timeout_s: float = 6.0
    overtake_speed_boost_kph: float = 15.0
    lane_width_m: float = 3.7
    require_highway: bool = True


settings = Settings("AutomaticOvertake")
