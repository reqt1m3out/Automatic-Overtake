from ETS2LA.UI import (
    ETS2LAPage,
    ETS2LAPageLocation,
    TitleAndDescription,
    Container,
    Tabs,
    Tab,
    styles,
    CheckboxWithTitleDescription,
    ComboboxWithTitleDescription,
    SliderWithTitleDescription,
    Text,
)
from ETS2LA.Utils.translator import _

from CataloguePlugins.AutomaticOvertake.settings import settings


class SettingsPage(ETS2LAPage):
    url = "/settings/automatic-overtake"
    location = ETS2LAPageLocation.SETTINGS
    title = _("Automatic Overtake")
    refresh_rate = -1

    def _to_float(self, value) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def handle_enabled(self, value):
        settings.enabled = value

    def handle_preferred_side(self, value):
        settings.preferred_side = value

    def handle_min_speed(self, value):
        settings.min_speed_kph = self._to_float(value)

    def handle_lead_distance(self, value):
        settings.min_lead_distance_m = self._to_float(value)

    def handle_speed_delta(self, value):
        settings.min_speed_delta_kph = self._to_float(value)

    def handle_intersection_buffer(self, value):
        settings.intersection_buffer_m = self._to_float(value)

    def handle_lane_front(self, value):
        settings.lane_clear_front_m = self._to_float(value)

    def handle_lane_rear(self, value):
        settings.lane_clear_rear_m = self._to_float(value)

    def handle_highway_requirement(self, value):
        settings.require_highway = value

    def handle_hold_duration(self, value):
        settings.hold_duration_s = self._to_float(value)

    def handle_return_clearance(self, value):
        settings.return_clearance_m = self._to_float(value)

    def handle_rear_time_gap(self, value):
        settings.rear_time_gap_s = self._to_float(value)

    def handle_speed_boost(self, value):
        settings.overtake_speed_boost_kph = self._to_float(value)

    def render(self):
        TitleAndDescription(
            title=_("Automatic Overtake"),
            description=_(
                "Controls overtaking manoeuvres using map steering data and adaptive cruise control."
            ),
        )

        with Tabs():
            with Tab(
                _("Automation"),
                container_style=styles.FlexVertical() + styles.Gap("18px"),
            ):
                CheckboxWithTitleDescription(
                    title=_("Enable Automatic Overtaking"),
                    description=_(
                        "Turns the automatic overtake controller on or off without disabling the plugin."
                    ),
                    default=settings.enabled,
                    changed=self.handle_enabled,
                )

                ComboboxWithTitleDescription(
                    title=_("Preferred Passing Side"),
                    description=_(
                        "Choose the side to pass slower traffic. Use right-hand passing for left-hand drive regions."
                    ),
                    options=["PassLeft", "PassRight"],
                    default=settings.preferred_side,
                    changed=self.handle_preferred_side,
                )

                CheckboxWithTitleDescription(
                    title=_("Require Divided Highway"),
                    description=_(
                        "Only allow overtakes on highway segments recognised by the map plugin."
                    ),
                    default=settings.require_highway,
                    changed=self.handle_highway_requirement,
                )

            with Tab(
                _("Thresholds"),
                container_style=styles.FlexVertical() + styles.Gap("14px"),
            ):
                with Container(styles.FlexVertical() + styles.Gap("10px")):
                    SliderWithTitleDescription(
                        title=_("Minimum Speed"),
                        description=_("Truck speed required before overtaking starts."),
                        default=float(settings.min_speed_kph),
                        min=20,
                        max=110,
                        step=5,
                        suffix=" km/h",
                        changed=self.handle_min_speed,
                    )
                    SliderWithTitleDescription(
                        title=_("Distance To Slower Vehicle"),
                        description=_(
                            "Overtake only if the ACC lead vehicle is within this range."
                        ),
                        default=float(settings.min_lead_distance_m),
                        min=10,
                        max=120,
                        step=5,
                        suffix=" m",
                        changed=self.handle_lead_distance,
                    )
                    SliderWithTitleDescription(
                        title=_("Speed Difference Requirement"),
                        description=_(
                            "Requires this difference between speed limit and actual speed."
                        ),
                        default=float(settings.min_speed_delta_kph),
                        min=5,
                        max=30,
                        step=1,
                        suffix=" km/h",
                        changed=self.handle_speed_delta,
                    )
                    SliderWithTitleDescription(
                        title=_("Monitoring Duration"),
                        description=_(
                            "Time to verify conditions are stable before initiating overtake."
                        ),
                        default=float(settings.hold_duration_s),
                        min=0.5,
                        max=5.0,
                        step=0.5,
                        suffix=" s",
                        changed=self.handle_hold_duration,
                    )
                    SliderWithTitleDescription(
                        title=_("Overtake Speed Boost"),
                        description=_(
                            "Extra speed allowed during overtake (sent to ACC as speed boost signal)."
                        ),
                        default=float(settings.overtake_speed_boost_kph),
                        min=0,
                        max=30,
                        step=5,
                        suffix=" km/h",
                        changed=self.handle_speed_boost,
                    )
                    SliderWithTitleDescription(
                        title=_("Intersection Buffer"),
                        description=_(
                            "Skip overtakes when a prefab or intersection is closer than this distance."
                        ),
                        default=float(settings.intersection_buffer_m),
                        min=60,
                        max=400,
                        step=10,
                        suffix=" m",
                        changed=self.handle_intersection_buffer,
                    )
                    SliderWithTitleDescription(
                        title=_("Adjacent Lane Front Clearance"),
                        description=_(
                            "Nearest forward distance required to keep the target lane clear."
                        ),
                        default=float(settings.lane_clear_front_m),
                        min=20,
                        max=120,
                        step=5,
                        suffix=" m",
                        changed=self.handle_lane_front,
                    )
                    SliderWithTitleDescription(
                        title=_("Adjacent Lane Rear Clearance"),
                        description=_(
                            "Minimum rear clearance (dynamic calculation uses speed-based time gap)."
                        ),
                        default=float(settings.lane_clear_rear_m),
                        min=5,
                        max=40,
                        step=1,
                        suffix=" m",
                        changed=self.handle_lane_rear,
                    )
                    SliderWithTitleDescription(
                        title=_("Rear Time Gap"),
                        description=_(
                            "Time gap for dynamic rear clearance calculation (seconds at current speed)."
                        ),
                        default=float(settings.rear_time_gap_s),
                        min=1.0,
                        max=4.0,
                        step=0.5,
                        suffix=" s",
                        changed=self.handle_rear_time_gap,
                    )
                    SliderWithTitleDescription(
                        title=_("Return Clearance"),
                        description=_(
                            "Distance overtaken vehicle must be behind before returning to original lane."
                        ),
                        default=float(settings.return_clearance_m),
                        min=15,
                        max=60,
                        step=5,
                        suffix=" m",
                        changed=self.handle_return_clearance,
                    )
                Text(
                    _(
                        "These values are applied immediately and will persist between launches."
                    ),
                    styles.Classname("text-xs text-muted-foreground"),
                )
