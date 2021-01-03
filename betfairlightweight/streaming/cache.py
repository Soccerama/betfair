import datetime
from typing import Union

from ..resources import BaseResource, MarketBook, CurrentOrders, MarketDefinition, Race
from ..enums import (
    StreamingOrderType,
    StreamingPersistenceType,
    StreamingSide,
    StreamingStatus,
)
from ..utils import create_date_string


class Available:
    """
    Data structure to hold prices/traded amount,
    designed to be as quick as possible.
    """

    def __init__(self, prices: list, deletion_select: int, reverse: bool = False):
        """
        :param list prices: Current prices
        :param int deletion_select: Used to decide if update should delete cache
        :param bool reverse: Used for sorting
        """
        self.prices = prices or []
        self.deletion_select = deletion_select
        self.reverse = reverse

        self.serialise = []
        self.sort()

    def sort(self, sort=True) -> None:
        if sort:  # limit expensive sort
            self.prices.sort(reverse=self.reverse)
        # avoiding dots / create local vars
        v_deletion_select = self.deletion_select - 1
        s_deletion_select = self.deletion_select
        self.serialise = [
            {
                "price": volume[v_deletion_select],
                "size": volume[s_deletion_select],
            }
            for volume in self.prices
        ]

    def clear(self) -> None:
        self.prices = []
        self.sort()

    def update(self, book_update: list) -> None:
        sort = False
        for book in book_update:
            for (count, trade) in enumerate(self.prices):
                if trade[0] == book[0]:
                    if book[self.deletion_select] == 0:
                        del self.prices[count]
                        break
                    else:
                        self.prices[count] = book
                        break
            else:
                if book[self.deletion_select] != 0:
                    # handles betfair bug,
                    # https://forum.developer.betfair.com/forum/sports-exchange-api/exchange-api/3425-streaming-bug
                    self.prices.append(book)
                    sort = True
        self.sort(sort)


class RunnerBook:
    def __init__(
        self,
        id: int,
        ltp: float = None,
        tv: float = None,
        trd: list = None,
        atb: list = None,
        batb: list = None,
        bdatb: list = None,
        atl: list = None,
        batl: list = None,
        bdatl: list = None,
        spn: float = None,
        spf: float = None,
        spb: list = None,
        spl: list = None,
        hc: int = 0,
        definition: dict = None,
    ):
        self.selection_id = id
        self.last_price_traded = ltp
        self.total_matched = tv
        self.traded = Available(trd, 1)
        self.available_to_back = Available(atb, 1, True)
        self.best_available_to_back = Available(batb, 2)
        self.best_display_available_to_back = Available(bdatb, 2)
        self.available_to_lay = Available(atl, 1)
        self.best_available_to_lay = Available(batl, 2)
        self.best_display_available_to_lay = Available(bdatl, 2)
        self.starting_price_back = Available(spb, 1)
        self.starting_price_lay = Available(spl, 1)
        self.starting_price_near = spn
        self.starting_price_far = spf
        self.handicap = hc
        self.definition = definition or {}
        self._definition_status = None
        self._definition_bsp = None
        self._definition_adjustment_factor = None
        self._definition_removal_date = None
        self.update_definition(self.definition)
        self.serialised = {}  # cache is king

    def update_definition(self, definition: dict) -> None:
        self.definition = definition
        # cache values used in serialisation to prevent duplicate <get>
        self._definition_status = self.definition.get("status")
        self._definition_bsp = self.definition.get("bsp")
        self._definition_adjustment_factor = self.definition.get("adjustmentFactor")
        self._definition_removal_date = self.definition.get("removalDate")

    def update_traded(self, traded_update: list) -> None:
        """:param traded_update: [price, size]"""
        if not traded_update:
            self.traded.clear()
        else:
            self.traded.update(traded_update)

    def serialise_available_to_back(self) -> list:
        if self.available_to_back.prices:
            return self.available_to_back.serialise
        elif self.best_display_available_to_back.prices:
            return self.best_display_available_to_back.serialise
        elif self.best_available_to_back.prices:
            return self.best_available_to_back.serialise
        else:
            return []

    def serialise_available_to_lay(self) -> list:
        if self.available_to_lay.prices:
            return self.available_to_lay.serialise
        elif self.best_display_available_to_lay.prices:
            return self.best_display_available_to_lay.serialise
        elif self.best_available_to_lay.prices:
            return self.best_available_to_lay.serialise
        return []

    def serialise(self) -> None:
        self.serialised = {
            "status": self._definition_status,
            "ex": {
                "tradedVolume": self.traded.serialise,
                "availableToBack": self.serialise_available_to_back(),
                "availableToLay": self.serialise_available_to_lay(),
            },
            "sp": {
                "nearPrice": self.starting_price_near,
                "farPrice": self.starting_price_far,
                "backStakeTaken": self.starting_price_back.serialise,
                "layLiabilityTaken": self.starting_price_lay.serialise,
                "actualSP": self._definition_bsp,
            },
            "adjustmentFactor": self._definition_adjustment_factor,
            "removalDate": self._definition_removal_date,
            "lastPriceTraded": self.last_price_traded,
            "handicap": self.handicap,
            "totalMatched": self.total_matched,
            "selectionId": self.selection_id,
        }


class MarketBookCache(BaseResource):
    def __init__(self, market_id, publish_time):
        super(MarketBookCache, self).__init__()
        self.market_id = market_id
        self.publish_time = publish_time
        self.total_matched = None
        self.market_definition = {}
        self._definition_bet_delay = None
        self._definition_version = None
        self._definition_complete = None
        self._definition_runners_voidable = None
        self._definition_status = None
        self._definition_bsp_reconciled = None
        self._definition_cross_matching = None
        self._definition_in_play = None
        self._definition_number_of_winners = None
        self._definition_number_of_active_runners = None
        self._definition_price_ladder_definition = None
        self._definition_key_line_description = None
        self.streaming_update = None
        self.runners = []
        self.runner_dict = {}
        self._number_of_runners = 0

    def update_cache(self, market_change: dict, publish_time: int) -> None:
        self.publish_time = publish_time
        self.streaming_update = market_change

        if "marketDefinition" in market_change:
            self._process_market_definition(market_change["marketDefinition"])

        if "tv" in market_change:
            self.total_matched = market_change["tv"]

        if "rc" in market_change:
            for new_data in market_change["rc"]:
                runner = self.runner_dict.get((new_data["id"], new_data.get("hc", 0)))
                if runner:
                    if "ltp" in new_data:
                        runner.last_price_traded = new_data["ltp"]
                    if "tv" in new_data:  # if runner removed tv: 0 is returned
                        runner.total_matched = new_data["tv"]
                    if "spn" in new_data:
                        runner.starting_price_near = new_data["spn"]
                    if "spf" in new_data:
                        runner.starting_price_far = new_data["spf"]
                    if "trd" in new_data:
                        runner.update_traded(new_data["trd"])
                    if "atb" in new_data:
                        runner.available_to_back.update(new_data["atb"])
                    if "atl" in new_data:
                        runner.available_to_lay.update(new_data["atl"])
                    if "batb" in new_data:
                        runner.best_available_to_back.update(new_data["batb"])
                    if "batl" in new_data:
                        runner.best_available_to_lay.update(new_data["batl"])
                    if "bdatb" in new_data:
                        runner.best_display_available_to_back.update(new_data["bdatb"])
                    if "bdatl" in new_data:
                        runner.best_display_available_to_lay.update(new_data["bdatl"])
                    if "spb" in new_data:
                        runner.starting_price_back.update(new_data["spb"])
                    if "spl" in new_data:
                        runner.starting_price_lay.update(new_data["spl"])
                else:
                    runner = self._add_new_runner(**new_data)
                runner.serialise()

    def _process_market_definition(self, market_definition: dict) -> None:
        self.market_definition = market_definition
        # cache values used in serialisation to prevent duplicate <get>
        self._definition_bet_delay = market_definition.get("betDelay")
        self._definition_version = market_definition.get("version")
        self._definition_complete = market_definition.get("complete")
        self._definition_runners_voidable = market_definition.get("runnersVoidable")
        self._definition_status = market_definition.get("status")
        self._definition_bsp_reconciled = market_definition.get("bspReconciled")
        self._definition_cross_matching = market_definition.get("crossMatching")
        self._definition_in_play = market_definition.get("inPlay")
        self._definition_number_of_winners = market_definition.get("numberOfWinners")
        self._definition_number_of_active_runners = market_definition.get(
            "numberOfActiveRunners"
        )
        self._definition_price_ladder_definition = market_definition.get(
            "priceLadderDefinition"
        )
        self._definition_key_line_description = market_definition.get(
            "keyLineDefinition"
        )
        # process runners
        for runner_definition in market_definition.get("runners", []):
            selection_id = runner_definition["id"]
            hc = runner_definition.get("hc", 0)
            runner = self.runner_dict.get((selection_id, hc))
            if runner:
                runner.update_definition(runner_definition)
            else:
                runner = self._add_new_runner(
                    id=selection_id, hc=hc, definition=runner_definition
                )
            runner.serialise()

    def _add_new_runner(self, **kwargs) -> RunnerBook:
        runner = RunnerBook(**kwargs)
        self.runners.append(runner)
        self._number_of_runners = len(self.runners)
        # update runner_dict
        self.runner_dict = {
            (runner.selection_id, runner.handicap): runner for runner in self.runners
        }
        return runner

    def create_resource(
        self, unique_id: int, lightweight: bool, snap: bool = False
    ) -> Union[dict, MarketBook]:
        data = self.serialise
        data["streaming_unique_id"] = unique_id
        data["streaming_update"] = self.streaming_update
        data["streaming_snap"] = snap
        if lightweight:
            return data
        else:
            return MarketBook(
                elapsed_time=(
                    datetime.datetime.utcnow() - self._datetime_updated
                ).total_seconds(),
                market_definition=MarketDefinition(**self.market_definition),
                **data
            )

    @property
    def closed(self) -> bool:
        if self.market_definition.get("status") == "CLOSED":
            return True
        else:
            return False

    @property
    def serialise(self) -> dict:
        """Creates standard market book json response,
        will contain missing data if EX_MARKET_DEF
        not incl.
        """
        return {
            "marketId": self.market_id,
            "totalAvailable": None,
            "isMarketDataDelayed": None,
            "lastMatchTime": None,
            "betDelay": self._definition_bet_delay,
            "version": self._definition_version,
            "complete": self._definition_complete,
            "runnersVoidable": self._definition_runners_voidable,
            "totalMatched": self.total_matched,
            "status": self._definition_status,
            "bspReconciled": self._definition_bsp_reconciled,
            "crossMatching": self._definition_cross_matching,
            "inplay": self._definition_in_play,
            "numberOfWinners": self._definition_number_of_winners,
            "numberOfRunners": self._number_of_runners,
            "numberOfActiveRunners": self._definition_number_of_active_runners,
            "runners": [runner.serialised for runner in self.runners],
            "publishTime": self.publish_time,
            "priceLadderDefinition": self._definition_price_ladder_definition,
            "keyLineDescription": self._definition_key_line_description,
            "marketDefinition": self.market_definition,  # used in lightweight
        }


class UnmatchedOrder:
    def __init__(
        self,
        id: str,
        p: float,
        s: float,
        side: str,
        status: str,
        ot: str,
        pd: int,
        sm: float,
        sr: float,
        sl: float,
        sc: float,
        sv: float,
        rfo: str,
        rfs: str,
        pt: str = None,
        md: str = None,
        avp: float = None,
        bsp: float = None,
        ld: int = None,
        rac: str = None,
        rc: str = None,
        lsrc: str = None,
        cd: int = None,
        **kwargs
    ):
        self.bet_id = id
        self.price = p
        self.size = s
        self.bsp_liability = bsp
        self.side = side
        self.status = status
        self.persistence_type = pt
        self.order_type = ot
        self.placed_date = BaseResource.strip_datetime(pd)
        self._placed_date_string = create_date_string(self.placed_date)
        self.matched_date = BaseResource.strip_datetime(md)
        self._matched_date_string = create_date_string(self.matched_date)
        self.average_price_matched = avp
        self.size_matched = sm
        self.size_remaining = sr
        self.size_lapsed = sl
        self.size_cancelled = sc
        self.size_voided = sv
        self.regulator_auth_code = rac
        self.regulator_code = rc
        self.reference_order = rfo
        self.reference_strategy = rfs
        self.lapsed_date = BaseResource.strip_datetime(ld)
        self._lapsed_date_string = create_date_string(self.lapsed_date)
        self.lapse_status_reason_code = lsrc
        self.cancelled_date = BaseResource.strip_datetime(cd)
        self._cancelled_date_string = create_date_string(self.cancelled_date)

    def serialise(self, market_id: str, selection_id: int, handicap: int) -> dict:
        return {
            "averagePriceMatched": self.average_price_matched or 0.0,
            "betId": self.bet_id,
            "bspLiability": self.bsp_liability,
            "handicap": handicap,
            "marketId": market_id,
            "matchedDate": self._matched_date_string,
            "orderType": StreamingOrderType[self.order_type].value,
            "persistenceType": StreamingPersistenceType[self.persistence_type].value
            if self.persistence_type
            else None,
            "placedDate": self._placed_date_string,
            "priceSize": {"price": self.price, "size": self.size},
            "regulatorAuthCode": self.regulator_auth_code,
            "regulatorCode": self.regulator_code,
            "selectionId": selection_id,
            "side": StreamingSide[self.side].value,
            "sizeCancelled": self.size_cancelled,
            "sizeLapsed": self.size_lapsed,
            "sizeMatched": self.size_matched,
            "sizeRemaining": self.size_remaining,
            "sizeVoided": self.size_voided,
            "status": StreamingStatus[self.status].value,
            "customerStrategyRef": self.reference_strategy,
            "customerOrderRef": self.reference_order,
            "lapsedDate": self._lapsed_date_string,
            "lapseStatusReasonCode": self.lapse_status_reason_code,
            "cancelledDate": self._cancelled_date_string,
        }


class OrderBookRunner:
    def __init__(
        self,
        id: int,
        fullImage: dict = None,
        ml: list = None,
        mb: list = None,
        uo: list = None,
        hc: int = 0,
        smc: dict = None,
    ):
        self.selection_id = id
        self.full_image = fullImage
        self.matched_lays = Available(ml, 1)
        self.matched_backs = Available(mb, 1)
        self.unmatched_orders = {i["id"]: UnmatchedOrder(**i) for i in uo} if uo else {}
        self.handicap = hc
        self.strategy_matches = smc

    def update_unmatched(self, unmatched_orders: list) -> None:
        for unmatched_order in unmatched_orders:
            self.unmatched_orders[unmatched_order["id"]] = UnmatchedOrder(
                **unmatched_order
            )

    def serialise_orders(self, market_id: str) -> list:
        orders = list(self.unmatched_orders.values())  # order may be added (#232)
        return [
            order.serialise(market_id, self.selection_id, self.handicap)
            for order in orders
        ]

    def serialise_matches(self) -> dict:
        return {
            "selectionId": self.selection_id,
            "matchedLays": self.matched_lays.serialise,
            "matchedBacks": self.matched_backs.serialise,
        }


class OrderBookCache(BaseResource):
    def __init__(self, **kwargs):
        super(OrderBookCache, self).__init__(**kwargs)
        self.publish_time = kwargs.get("publish_time")
        self.market_id = kwargs.get("id")
        self.closed = kwargs.get("closed")
        self.streaming_update = None
        self.runners = {}  # (selectionId, handicap):

    def update_cache(self, order_book: dict, publish_time: int) -> None:
        self._datetime_updated = self.strip_datetime(publish_time)
        self.publish_time = publish_time
        self.streaming_update = order_book
        if "closed" in order_book:
            self.closed = order_book["closed"]

        for order_changes in order_book.get("orc", []):
            selection_id = order_changes["id"]
            handicap = order_changes.get("hc", 0)
            full_image = order_changes.get("fullImage")
            _lookup = (selection_id, handicap)
            runner = self.runners.get(_lookup)
            if full_image or runner is None:
                self.runners[_lookup] = OrderBookRunner(**order_changes)
            else:
                if "ml" in order_changes:
                    runner.matched_lays.update(order_changes["ml"])
                if "mb" in order_changes:
                    runner.matched_backs.update(order_changes["mb"])
                if "uo" in order_changes:
                    runner.update_unmatched(order_changes["uo"])

    def create_resource(
        self, unique_id: int, lightweight: bool, snap: bool = False
    ) -> Union[dict, CurrentOrders]:
        data = self.serialise
        data["streaming_unique_id"] = unique_id
        data["streaming_update"] = self.streaming_update
        data["streaming_snap"] = snap
        if lightweight:
            return data
        else:
            return CurrentOrders(
                elapsed_time=(
                    datetime.datetime.utcnow() - self._datetime_updated
                ).total_seconds(),
                publish_time=self.publish_time,
                **data
            )

    @property
    def serialise(self) -> dict:
        runners = list(self.runners.values())  # runner may be added
        orders, matches = [], []
        for runner in runners:
            orders.extend(runner.serialise_orders(self.market_id))
            matches.append(runner.serialise_matches())
        return {"currentOrders": orders, "matches": matches, "moreAvailable": False}


class RunnerChange:
    def __init__(self, change: dict):
        self.change = change


class RaceCache(BaseResource):
    def __init__(self, **kwargs):
        super(RaceCache, self).__init__(**kwargs)
        self.publish_time = kwargs.get("publish_time")
        self.market_id = kwargs.get("mid")
        self.race_id = kwargs.get("id")
        self.rpc = kwargs.get("rpc")  # RaceProgressChange
        self.rrc = [RunnerChange(i) for i in kwargs.get("rrc", [])]  # RaceRunnerChange
        self.streaming_update = None

    def update_cache(self, update: dict, publish_time: int) -> None:
        self._datetime_updated = self.strip_datetime(publish_time)
        self.publish_time = publish_time
        self.streaming_update = update

        if "rpc" in update:
            self.rpc = update["rpc"]

        if "rrc" in update:
            runner_dict = {runner.change["id"]: runner for runner in self.rrc}

            for runner_update in update["rrc"]:
                runner = runner_dict.get(runner_update["id"])
                if runner:
                    runner.change = runner_update
                else:
                    self.rrc.append(RunnerChange(runner_update))

    def create_resource(
        self, unique_id: int, lightweight: bool, snap: bool = False
    ) -> Union[dict, Race]:
        data = self.serialise
        data["streaming_unique_id"] = unique_id
        data["streaming_update"] = self.streaming_update
        data["streaming_snap"] = snap
        if lightweight:
            return data
        else:
            return Race(
                elapsed_time=(
                    datetime.datetime.utcnow() - self._datetime_updated
                ).total_seconds(),
                **data
            )

    @property
    def serialise(self) -> dict:
        return {
            "pt": self.publish_time,
            "mid": self.market_id,
            "id": self.race_id,
            "rpc": self.rpc,
            "rrc": [runner.change for runner in self.rrc],
        }
