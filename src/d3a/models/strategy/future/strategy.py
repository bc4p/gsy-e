"""
Copyright 2018 Grid Singularity
This file is part of D3A.
This program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not,
see <http://www.gnu.org/licenses/>.
"""

from typing import TYPE_CHECKING, List

from d3a_interface.constants_limits import GlobalConfig
from pendulum import duration, DateTime

from d3a.constants import FutureTemplateStrategiesConstants
from d3a.models.base import AssetType
from d3a.models.strategy.update_frequency import (TemplateStrategyBidUpdater,
                                                  TemplateStrategyOfferUpdater)

if TYPE_CHECKING:
    from d3a.models.area import Area
    from d3a.models.strategy import BidEnabledStrategy
    from d3a.models.market.future import FutureMarkets


class FutureTemplateStrategyBidUpdater(TemplateStrategyBidUpdater):
    """Version of TemplateStrategyBidUpdater class for future markets"""

    @property
    def _time_slot_duration_in_seconds(self) -> int:
        return GlobalConfig.FUTURE_MARKET_DURATION_HOURS * 60 * 60

    @staticmethod
    def get_all_markets(area: "Area") -> List["FutureMarkets"]:
        """Override to return list of future markets"""
        return [area.future_markets]

    @staticmethod
    def get_all_time_slots(area: "Area") -> List[DateTime]:
        """Override to return all future market available time slots"""
        return area.future_markets.market_time_slots

    def update(self, market: "FutureMarkets", strategy: "BidEnabledStrategy") -> None:
        """Update the price of existing bids to reflect the new rates."""
        for time_slot in strategy.area.future_markets.market_time_slots:
            if self.time_for_price_update(strategy, time_slot):
                if strategy.are_bids_posted(market.id, time_slot):
                    strategy.update_bid_rates(market, self.get_updated_rate(time_slot))


class FutureTemplateStrategyOfferUpdater(TemplateStrategyOfferUpdater):
    """Version of TemplateStrategyOfferUpdater class for future markets"""

    @property
    def _time_slot_duration_in_seconds(self) -> int:
        return GlobalConfig.FUTURE_MARKET_DURATION_HOURS * 60 * 60

    @staticmethod
    def get_all_markets(area: "Area") -> List["FutureMarkets"]:
        """Override to return list of future markets"""
        return [area.future_markets]

    @staticmethod
    def get_all_time_slots(area: "Area") -> List[DateTime]:
        """Override to return all future market available time slots"""
        return area.future_markets.market_time_slots

    def update(self, market: "FutureMarkets", strategy: "BidEnabledStrategy") -> None:
        """Update the price of existing offers to reflect the new rates."""
        for time_slot in strategy.area.future_markets.market_time_slots:
            if self.time_for_price_update(strategy, time_slot):
                if strategy.are_offers_posted(market.id):
                    strategy.update_offer_rates(market, self.get_updated_rate(time_slot))


class FutureMarketStrategyInterface:
    """Dummy/empty class that does not provide concrete implementation of the methods.
    Is needed in order to disable the implementation of the future market strategy
    when future markets are disabled by configuration."""
    def __init__(self, *args, **kwargs):
        pass

    def event_market_cycle(self, strategy: "BidEnabledStrategy") -> None:
        """Base class method for handling the market cycle"""

    def event_tick(self, strategy: "BidEnabledStrategy") -> None:
        """Base class method for handling the tick"""


class FutureMarketStrategy(FutureMarketStrategyInterface):
    """Manages bid/offer trading strategy for the future markets, for a single asset."""
    def __init__(self,
                 initial_buying_rate: float, final_buying_rate: float,
                 initial_selling_rate: float, final_selling_rate: float):
        """
        Args:
            initial_buying_rate: Initial rate of the future bids
            final_buying_rate: Final rate of the future bids
            initial_selling_rate: Initial rate of the future offers
            final_selling_rate: Final rate of the future offers
        """
        super().__init__()

        self._update_interval = FutureTemplateStrategiesConstants.UPDATE_INTERVAL_MIN
        self._bid_updater = FutureTemplateStrategyBidUpdater(
                initial_rate=initial_buying_rate,
                final_rate=final_buying_rate,
                fit_to_limit=True,
                energy_rate_change_per_update=None,
                update_interval=duration(minutes=self._update_interval),
                rate_limit_object=min)

        self._offer_updater = FutureTemplateStrategyOfferUpdater(
                initial_rate=initial_selling_rate,
                final_rate=final_selling_rate,
                fit_to_limit=True,
                energy_rate_change_per_update=None,
                update_interval=duration(minutes=self._update_interval),
                rate_limit_object=max)

    def event_market_cycle(self, strategy: "BidEnabledStrategy") -> None:
        """
        Should be called by the event_market_cycle of the asset strategy class, posts
        settlement bids and offers on markets that do not have posted bids and offers yet
        Args:
            strategy: Strategy object of the asset

        Returns: None

        """
        if not strategy.area.future_markets:
            return
        self._bid_updater.update_and_populate_price_settings(strategy.area)
        self._offer_updater.update_and_populate_price_settings(strategy.area)
        for time_slot in strategy.area.future_markets.market_time_slots:
            if strategy.asset_type == AssetType.CONSUMER:
                required_energy_kWh = strategy.state.get_energy_requirement_Wh(time_slot) / 1000.0
                self._post_consumer_first_bid(strategy, time_slot, required_energy_kWh)
            elif strategy.asset_type == AssetType.PRODUCER:
                available_energy_kWh = strategy.state.get_available_energy_kWh(time_slot)
                self._post_producer_first_offer(strategy, time_slot, available_energy_kWh)
            elif strategy.asset_type == AssetType.PROSUMER:
                available_energy_sell_kWh = strategy.get_available_energy_to_sell_kWh(time_slot)
                available_energy_buy_kWh = strategy.get_available_energy_to_buy_kWh(time_slot)
                self._post_producer_first_offer(strategy, time_slot, available_energy_sell_kWh)
                self._post_consumer_first_bid(strategy, time_slot, available_energy_buy_kWh)
            else:
                assert False, ("Strategy %s has to be producer or consumer to be able to "
                               "participate in the future market.", strategy.owner.name)

        self._bid_updater.increment_update_counter_all_markets(strategy)
        self._offer_updater.increment_update_counter_all_markets(strategy)

    def _post_consumer_first_bid(
            self, strategy: "BidEnabledStrategy", time_slot: DateTime,
            available_buy_energy_kWh: float) -> None:

        if available_buy_energy_kWh <= 0.0:
            return
        if strategy.get_posted_bids(strategy.area.future_markets, time_slot):
            return
        strategy.post_bid(
            market=strategy.area.future_markets,
            energy=available_buy_energy_kWh,
            price=available_buy_energy_kWh * self._bid_updater.initial_rate[time_slot],
            time_slot=time_slot)

    def _post_producer_first_offer(
            self, strategy: "BidEnabledStrategy", time_slot: DateTime,
            available_sell_energy_kWh: float) -> None:
        if available_sell_energy_kWh <= 0.0:
            return
        if strategy.get_posted_offers(strategy.area.future_markets, time_slot):
            return
        strategy.post_offer(
            market=strategy.area.future_markets,
            replace_existing=False,
            energy=available_sell_energy_kWh,
            price=available_sell_energy_kWh * self._offer_updater.initial_rate[time_slot],
            time_slot=time_slot
        )

    def event_tick(self, strategy: "BidEnabledStrategy") -> None:
        """
        Update posted settlement bids and offers on market tick.
        Order matters here:
            - FIRST: the bids and offers need to be updated (update())
            - SECOND: the update counter has to be increased (increment_update_counter_all_markets)
        Args:
            strategy: Strategy object of the asset

        Returns: None

        """
        if not strategy.area.future_markets:
            return
        self._bid_updater.update(strategy.area.future_markets, strategy)
        self._offer_updater.update(strategy.area.future_markets, strategy)

        self._bid_updater.increment_update_counter_all_markets(strategy)
        self._offer_updater.increment_update_counter_all_markets(strategy)


def future_market_strategy_factory(
        initial_buying_rate: float = FutureTemplateStrategiesConstants.INITIAL_BUYING_RATE,
        final_buying_rate: float = FutureTemplateStrategiesConstants.FINAL_BUYING_RATE,
        initial_selling_rate: float = FutureTemplateStrategiesConstants.INITIAL_SELLING_RATE,
        final_selling_rate: float = FutureTemplateStrategiesConstants.FINAL_SELLING_RATE
) -> FutureMarketStrategyInterface:
    """
    Factory method for creating the future market trading strategy. Creates an object of a
    class with empty implementation if the future market is disabled, with the real
    implementation otherwise
    Args:
        initial_buying_rate: Initial rate of the future bids
        final_buying_rate: Final rate of the future bids
        initial_selling_rate: Initial rate of the future offers
        final_selling_rate: Final rate of the future offers

    Returns: Future strategy object

    """
    if GlobalConfig.FUTURE_MARKET_DURATION_HOURS > 0:
        return FutureMarketStrategy(
            initial_buying_rate, final_buying_rate,
            initial_selling_rate, final_selling_rate)
    return FutureMarketStrategyInterface(
        initial_buying_rate, final_buying_rate,
        initial_selling_rate, final_selling_rate
    )
