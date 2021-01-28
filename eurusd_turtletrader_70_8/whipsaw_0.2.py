#####################################################
import datetime
from ib_insync import *
from ibapi import *
import logging
import pytz
import sys
import pandas as pd
import pandas_ta as ta
from pathlib import Path
from collections import OrderedDict
import json

#####################################################
# Algorithmic strategy class for interactive brokers:
class IBAlgoStrategy(object):
    """
    Algorithmic trading strategy for Interactive Brokers
    """
#####################################################
    def __init__(self):
        """Initialize Algorithm"""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.handler = logging.FileHandler('IBKRTradingAlgorithm.log')
        self.handler.setLevel(logging.INFO)
        self.formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.handler.setFormatter(self.formatter)
        self.logger.addHandler(self.handler)
        self.logger.info('Starting log at {}'.format(datetime.datetime.now()))

        # Connect to IB
        self.ib = self.connect()

        # Create empty list of instruments
        self.instruments = []

        # Run main loop
        self.run()

#####################################################
    def run(self):
        """Run logic for today's trading"""
        self.log()
        start_time = datetime.datetime.now(tz=pytz.timezone('Asia/Shanghai'))
        self.log('Beginning to run trading algorithm at {} HKT'
                 .format(start_time))

        for instrument in self.instruments:
            # Initial variable setup:
            local_symbol = instrument.localSymbol
            self.log('(1) Running initial variable setup for {}'
                     .format(instrument.localSymbol))

            # Get indicators
            indicators = self.get_indicators(instrument)

            # Init vars related to position sizing.
            base_exchange = self.get_base_exchange(instrument)
            max_equity_at_risk = self.get_max_equity_at_risk() / base_exchange
            sl_size = self.get_atr_multiple(instrument, multiplier=0.5)
            max_unit_size = max_equity_at_risk / sl_size
            self.log("Max equity at risk = {} units of {}"
                     .format(max_equity_at_risk, local_symbol))
            self.log("Max equity at risk = {} units of USD"
                     .format(max_equity_at_risk * base_exchange))
            self.log("Max unit size = {} units of {}"
                     .format(max_unit_size, local_symbol))

            # Init vars related to current unit/position.
            # All vars are in LOCAL currency!
            is_short = False
            is_long = False
            unit_full = True
            current_unit_size = self.get_cash_balance(instrument)
            if current_unit_size > float(100):
                is_long = True
            if current_unit_size < float(-100):
                is_short = True
            if abs(current_unit_size) < abs(max_unit_size) and (is_long
                                                                or is_short):
                unit_full = False

            # Save unit info to json:
            self.save_unit_info_to_json(local_symbol=local_symbol,
                                        max_unit_size=max_unit_size,
                                        current_unit_size=current_unit_size,
                                        sl_size=sl_size,
                                        base_exchange=base_exchange)

            self.log('Finished initial variable setup for {}'
                     .format(local_symbol))

            if not is_long and not is_short:
                self.log("(2) No open unit found for {}. \
                         Cancelling previously placed orders & \
                         clearing json data."
                         .format(local_symbol))
                # Logic for entering new unit.
                # Clear all json data and cancel all trades for this instrument:
                self.clear_orders_from_json(local_symbol)
                for o in self.get_open_trades(instrument):
                    self.ib.cancelOrder(o.order)
                self.log("Finished cancelling orders & clearing json data.")

                # Create unit info for new unit:
                self.log("(3) Creating new unit json data for {}"
                         .format(local_symbol))
                initial_entry_info = self.generate_initial_entry_info(
                    instrument)
                long_entry = initial_entry_info["long_entry"]
                short_entry = initial_entry_info["short_entry"]
                self.save_unit_info_to_json(local_symbol=local_symbol,
                                            long_entry=long_entry,
                                            short_entry=short_entry)
                self.log("Finished creating new unit json data for {}"
                         .format(local_symbol))

                # Create initial entry orders incl. sls and exit
                self.log("(4) Creating and placing initial entry orders \
                         for new {} unit".format(local_symbol))
                orders = self.create_initial_entry_orders(instrument)
                for o in orders:
                    self.log("Placing order {}".format(o.orderRef))
                    self.ib.placeOrder(instrument, o)
                    self.ib.sleep(1)
                self.log("Finished creating and placing initial entry orders \
                         for new {} unit".format(local_symbol))

            elif not unit_full:
                self.log("(3) Found open unit for {}. \
                         Recalculating exit all price for unit."
                         .format(local_symbol))

                exit_all_price = None
                if is_long:
                    exit_all_price = \
                        self.adjust_for_price_increments(instrument,
                                                         indicators
                                                         ['short_dcl']
                                                         [(indicators
                                                           .axes[0]
                                                           .stop - 1)])
                elif is_short:
                    exit_all_price = \
                        self.adjust_for_price_increments(instrument,
                                                         indicators
                                                         ['short_dcu']
                                                         [(indicators
                                                           .axes[0]
                                                           .stop - 1)])
                assert (exit_all_price is not None), \
                    "Exit all price for unit cannot be set to None!"
                self.log("Finished updating unit-wide exit all price.")

                self.log("(5) Updating json unit data & \
                         initial entry data if needed")
                self.save_unit_info_to_json(local_symbol=local_symbol,
                                            current_unit_size=current_unit_size,
                                            base_exchange=base_exchange,
                                            sl_size=sl_size,
                                            is_short=is_short,
                                            is_long=is_long,
                                            exit_all_price=exit_all_price)
                self.log("Finished updating unit data")

                unit_data = self.get_data_from_json()[local_symbol]
                if unit_data["entryInfo"]["entryA"]["totalQuantity"] == 0:
                    self.log("Current unit has been identified as new. Updating \
                             initial entry json data")
                    entry_a = unit_data["unitInfo"]["longEntry"] if is_long \
                        else unit_data["unitInfo"]["shortEntry"]
                    self.save_order_data_to_json(local_symbol=local_symbol,
                                                 order_json_tag="entryA",
                                                 action=entry_a["action"],
                                                 total_quantity=entry_a
                                                 ["totalQuantity"],
                                                 order_ref=entry_a["orderRef"],
                                                 sl_price=entry_a["slPrice"],
                                                 order_type=entry_a["orderType"],
                                                 tif=entry_a["tif"],
                                                 transmit=entry_a["transmit"],
                                                 price_condition=entry_a
                                                 ["priceCondition"],
                                                 is_more=entry_a["isMore"])
                    self.log("Finished updating initial entry data")
                else:
                    self.log("Current unit is not new and no initial entry data needs \
                             to be updated.")

                self.log("(6) Cancelling previously placed orders")
                for o in self.get_open_trades(instrument):
                    self.ib.cancelOrder(o.order)
                self.log("Finished cancelling open orders")

                self.log("(7) Calculating remaining compound orders \
                         before unit becomes full.")
                remaining_orders = None
                current_unit_size = unit_data["unitInfo"]["currentUnitSize"]
                max_unit_size = unit_data["unitInfo"]["maxUnitSize"]
                max_entry_size = max_unit_size / 4
                self.log("current unit: {}".format(current_unit_size))
                self.log("max unit: {}".format(max_unit_size))
                self.log("max entry: {}".format(max_entry_size))
                remaining_orders = round((max_unit_size - current_unit_size)
                                         / max_entry_size)
                assert (remaining_orders < 4), \
                    "Remaining orders in unit must be < 4!"
                assert (remaining_orders > 0), \
                    "Remaining orders in unit <= 0, \
                     but unit found to be not full!"
                self.log("Found {} compound orders can be made \
                          before unit is full.".format(remaining_orders))

                self.log("(8) Replacing stoploss \
                    and exit orders for filled entries.")
                unit_data = self.get_data_from_json()[local_symbol]
                exit_price_condition = unit_data["unitInfo"]["exitAllPrice"]
                for i in range(4 - remaining_orders):
                    entry_data = unit_data["entryData"].items()[i]
                    sl_price_condition = entry_data["slPrice"]
                    total_quantity = entry_data["totalQuantity"]
                    order_type = entry_data["orderType"]
                    tif = entry_data["tif"]
                    sl_order_ref = local_symbol + "sl" \
                        + entry_data["orderRef"][-1:]
                    exit_order_ref = local_symbol + "exit" \
                        + entry_data["orderRef"][-1:]
                    action = "BUY" if entry_data["action"] \
                        is "SELL" else "SELL"
                    is_more = True if entry_data["isMore"] \
                        is False else False

                    sl_order = self.create_order(instrument=instrument,
                                                 order_id=self
                                                 .ib.client.getReqId(),
                                                 action=action,
                                                 order_type=order_type,
                                                 tif=tif,
                                                 total_quantity=total_quantity,
                                                 transmit=False,
                                                 price_condition=sl_price_condition,
                                                 order_ref=sl_order_ref,
                                                 is_more=is_more)
                    exit_order = self.create_order(instrument=instrument,
                                                   order_id=self
                                                   .ib.client.getReqId(),
                                                   action=action,
                                                   order_type=order_type,
                                                   tif=tif,
                                                   total_quantity=total_quantity,
                                                   transmit=True,
                                                   price_condition=exit_price_condition,
                                                   order_ref=exit_order_ref,
                                                   is_more=is_more)

                    oca = [sl_order, exit_order]

                    self.ib.oneCancelsAll(orders=oca,
                                          ocaGroup="OCA_"
                                          + str(instrument.localSymbol)
                                          + str(self.ib.client.getReqId()),
                                          ocaType=1)

                    for o in oca:
                        self.log("Placing order {}".format(o.orderRef))
                        self.ib.placeOrder(instrument, o)
                        self.ib.sleep(1)
                self.log("Finished replacing stoploss \
                    and exit  orders for filled entries.")

                self.log("(9) Creating json data for compound orders.")
                compound_order_json_tags = []
                if remaining_orders >= 1:
                    compound_order_json_tags.append("entryB")
                elif remaining_orders >= 2:
                    compound_order_json_tags.append("entryC")
                elif remaining_orders == 3:
                    compound_order_json_tags.append("entryD")
                self.generate_compound_entry_info(instrument,
                                                  compound_order_json_tags)
                self.log("Finished creating json data for compound orders.")

                self.log("(10) Creating and placing compound orders")
                orders = []
                for t in compound_order_json_tags:
                    unit_leg = self.create_unit_leg(t, instrument)
                    for o in unit_leg:
                        orders.append(unit_leg[o])

                for o in orders:
                    self.log("Placing order {}".format(o.orderRef))
                    self.ib.placeOrder(instrument, o)
                    self.ib.sleep(1)
                self.log("Finished creating and placing compound orders")

#####################################################
    def get_max_equity_at_risk(self, multiplier=0.02):
        """Returns maximum unit equity at risk size (defaulty 2% of portfolio),
        in base currency"""
        max_equity_at_risk = 0
        for v in self.ib.accountSummary():
            if v.currency == 'BASE' and v.tag == 'CashBalance':
                max_equity_at_risk = float(v.value) * float(multiplier)
        return max_equity_at_risk

#####################################################
    def generate_compound_entry_info(self, instrument,
                                     compound_order_json_tags):
        """Generates compound entry information that can be saved to json."""
        unit_data = self.get_data_from_json()[instrument.localSymbol]
        sl_size = self.get_atr_multiple(instrument)
        total_quantity = round(unit_data["unitInfo"]["maxUnitSize"] / 4)
        is_long = unit_data["unitInfo"]["isLong"]
        is_short = unit_data["unitInfo"]["isShort"]
        assert (is_long is not is_short), \
            "Unit cannot be both long and short simultaneously!"

        compound_orders = {}
        for r in compound_order_json_tags:
            assert (r in ["entryB", "entryC", "entryD"]), \
                "Invalid compound order json tag!"
            offset = None
            if r == "entryB":
                offset = 1
                last_entry = unit_data["entryInfo"]["entryA"]
            elif r == "entryC":
                offset = 2
                last_entry = unit_data["entryInfo"]["entryB"]
            elif r == "entryD":
                offset = 3
                last_entry = unit_data["entryInfo"]["entryC"]
            assert (offset is not None), \
                "Offset cannot be set to None!"

            price_condition = None
            sl_price = None
            action = None
            is_more = None
            if is_long:
                price_condition = last_entry["priceCondition"] \
                    + offset * self.get_atr_multiple(instrument)
                sl_price = price_condition - sl_size
                action = "BUY"
                is_more = True
            elif is_short:
                price_condition = - last_entry["priceCondition"] \
                    - offset * self.get_atr_multiple(instrument)
                sl_price = price_condition + sl_size
                action = "SELL"
                is_more = False

            compound_orders[r] = {
                "action": action,
                "orderType": "MKT",
                "tif": "GTC",
                "totalQuantity": total_quantity,
                "transmit": False,
                "priceCondition": price_condition,
                "orderRef": instrument.localSymbol + r,
                "isMore": is_more,
                "slPrice": sl_price
            }

        return compound_orders

#####################################################
    def generate_initial_entry_info(self, instrument):
        """Generates initial entry information that can be saved to json."""
        indicators = self.get_indicators(instrument)
        sl_size = self.get_atr_multiple(instrument)
        total_quantity = self.set_position_size(instrument)
        long_price_condition = \
            self.adjust_for_price_increments(instrument,
                                             indicators
                                             ['long_dcu']
                                             [(indicators.axes[0]
                                              .stop - 1)])
        short_price_condition = \
            self.adjust_for_price_increments(instrument,
                                             indicators
                                             ['long_dcl']
                                             [(indicators.axes[0]
                                              .stop - 1)])

        long_sl_price = long_price_condition - sl_size
        short_sl_price = short_price_condition + sl_size

        long_entry = {
            "action": "BUY",
            "orderType": "MKT",
            "tif": "GTC",
            "totalQuantity": total_quantity,
            "transmit": False,
            "priceCondition": long_price_condition,
            "orderRef": instrument.localSymbol + "entryA",
            "isMore": True,
            "slPrice": long_sl_price
        }

        short_entry = {
            "action": "SELL",
            "orderType": "MKT",
            "tif": "GTC",
            "totalQuantity": total_quantity,
            "transmit": False,
            "priceCondition": short_price_condition,
            "orderRef": instrument.localSymbol + "entryA",
            "isMore": False,
            "slPrice": short_sl_price
        }

        return {"long_entry": long_entry,
                "short_entry": short_entry}

#####################################################
    def save_order_data_to_json(self, local_symbol, order_json_tag, action,
                                total_quantity, order_ref, sl_price,
                                order_type="MKT", tif="GTC", transmit=False,
                                **kwargs):
        """Save passed information to json."""
        price_condition = kwargs.get('price_condition', False),
        is_more = kwargs.get('is_more', False)
        assert (action in ["BUY", "SELL"]), "Invalid action passed."
        assert (total_quantity > 0), "Total quantity must be > 0."
        assert (sl_price > 0), "SL price must be > 0"
        assert (order_ref != ""), "Order reference cannot be blank."

        data_dict = self.get_data_from_json()
        assert (order_json_tag in data_dict[local_symbol]["entryInfo"]), \
            "Incorrect json tag passed."

        order_info = data_dict[local_symbol]["entryInfo"][order_json_tag]
        order_info["action"] = action
        order_info["orderType"] = order_type
        order_info["tif"] = tif
        order_info["totalQuantity"] = total_quantity
        order_info["transmit"] = transmit
        order_info["priceCondition"] = price_condition
        order_info["orderRef"] = order_ref
        order_info["isMore"] = is_more
        order_info["slPrice"] = sl_price

        data_dict[local_symbol][order_json_tag] = order_info
        self.save_data_to_json(data_dict)

#####################################################
    def save_unit_info_to_json(self, local_symbol, **kwargs):
        """Save passed information to json. json tags that are skipped
        are ignored, not cleared. Note that long and short entry need
        to be input as a dict in the format:
        {
            "action": "",
            "orderType": "",
            "tif": "",
            "totalQuantity": 0,
            "transmit": false,
            "priceCondition": 0,
            "orderRef": "",
            "isMore": false,
            "slPrice": 0
        }"""
        max_unit_size = kwargs.get('max_unit_size', False)
        current_unit_size = kwargs.get('current_unit_size', False)
        exit_all_price = kwargs.get('exit_all_price', False)
        is_short = kwargs.get('is_short', False)
        is_long = kwargs.get('is_long', False)
        long_entry = kwargs.get('long_entry', False)
        short_entry = kwargs.get('short_entry', False)
        sl_size = kwargs.get('sl_size', False)
        base_exchange = kwargs.get('base_exchange', False)

        data_dict = self.get_data_from_json()
        unit_info = data_dict[local_symbol]["unitInfo"]

        if max_unit_size:
            unit_info["maxUnitSize"] = max_unit_size
        if current_unit_size:
            unit_info["currentUnitSize"] = current_unit_size
        if exit_all_price:
            unit_info["exitAllPrice"] = exit_all_price
        if is_long:
            unit_info["isLong"] = is_long
        if is_short:
            unit_info["isShort"] = is_short
        if sl_size:
            unit_info["slSize"] = sl_size
        if base_exchange:
            unit_info["baseExchange"] = base_exchange
        if long_entry:
            unit_info["longEntry"] = long_entry
        if short_entry:
            unit_info["shortEntry"] = short_entry

        data_dict[local_symbol]["unitInfo"] = unit_info
        self.save_data_to_json(data_dict)

#####################################################
    def clear_unit_info_from_json(self, local_symbol):
        """Clear all unit-specific information from json"""
        data_dict = self.get_data_from_json()
        unit_info = data_dict[local_symbol]["unitInfo"]
        unit_info["maxUnitSize"] = 0
        unit_info["currentUnitSize"] = 0
        unit_info["isLong"] = ""
        unit_info["isShort"] = ""
        unit_info["slSize"] = 0
        unit_info["baseExchange"] = 0
        unit_info["longEntry"] = {
            "action": "",
            "orderType": "",
            "tif": "",
            "totalQuantity": 0,
            "transmit": False,
            "priceCondition": 0,
            "orderRef": "",
            "isMore": False,
            "slPrice": 0
        }
        unit_info["shortEntry"] = {
            "action": "",
            "orderType": "",
            "tif": "",
            "totalQuantity": 0,
            "transmit": False,
            "priceCondition": 0,
            "orderRef": "",
            "isMore": False,
            "slPrice": 0
        }
        data_dict[local_symbol]["unitInfo"] = unit_info
        self.save_data_to_json(data_dict)

#####################################################
    def clear_orders_from_json(self, local_symbol, order_name_list=["entryA",
                                                                    "entryB",
                                                                    "entryC",
                                                                    "entryD",
                                                                    "slA",
                                                                    "slB",
                                                                    "slC",
                                                                    "slD"]):
        """Input a list of orders and the relevant symbol, and clear
        their data from json. Leave order name list blank to clear all
        orders."""
        data_dict = self.get_data_from_json()
        for order_name in order_name_list:
            for order_data in data_dict[local_symbol]["entryInfo"]:
                if order_data == order_name:
                    order_data = {
                        "action": "",
                        "orderType": "",
                        "tif": "",
                        "totalQuantity": 0,
                        "transmit": False,
                        "priceCondition": 0,
                        "orderRef": "",
                        "isMore": False,
                        "slPrice": 0
                    }
                    self.save_data_to_json(data_dict)

#####################################################
    def get_data_from_json(self):
        this_path = Path(__file__)
        entry_data_path = Path(this_path.parent, 'entry_data.json')
        with entry_data_path.open(encoding='utf-8') as entry_data_file:
            entry_dict = json.load(entry_data_file, object_pairs_hook=OrderedDict)
        return entry_dict

#####################################################
    def save_data_to_json(self, data_dict):
        """Save data to json"""
        with open("entry_data.json", "w", encoding="utf-8") as f:
            json.dump(data_dict, f, indent=4, ensure_ascii=False)

#####################################################
    def create_initial_entry_orders(self, instrument):
        """Creates initial long & short order entries with SL and exits"""
        # Create initial short order entries:
        long_entry_attempts = self.create_unit_leg("longEntry",
                                                   instrument)

        # Create initial short order entries:
        short_entry_attempts = self.create_unit_leg("shortEntry",
                                                    instrument)

        # Put long and short order entries into OCA:
        self.ib.oneCancelsAll(orders=[long_entry_attempts["entry_order"],
                                      short_entry_attempts["entry_order"]],
                              ocaGroup="OCA_"
                              + str(instrument.localSymbol)
                              + str(self.ib.client.getReqId()),
                              ocaType=1)

        # Combine and return orders:
        orders = []
        for o in long_entry_attempts:
            orders.append(long_entry_attempts[o])
        for o in short_entry_attempts:
            orders.append(short_entry_attempts[o])
        return orders

#####################################################
    def create_unit_leg(self, entry_json_id, instrument):
        """Create an order for an entry, sl, and exit
        based on input from json file entry_data"""
        data_dict = self.get_data_from_json()[instrument.localSymbol]
        entry_data = {}
        if "long" in entry_json_id:
            entry_data = data_dict["unitInfo"]["longEntry"]
        elif "short" in entry_json_id:
            entry_data = data_dict["unitInfo"]["shortEntry"]
        else:
            entry_data = data_dict["entryInfo"][entry_json_id]
        entry_action = entry_data["action"]
        order_type = entry_data["orderType"]
        total_quantity = entry_data["totalQuantity"]
        entry_price_condition = entry_data["priceCondition"]
        sl_price_condition = entry_data["slPrice"]
        entry_order_ref = entry_data["orderRef"]
        sl_order_ref = instrument.localSymbol + "sl" + entry_order_ref[-1:]
        exit_order_ref = instrument.localSymbol + "exit" + entry_order_ref[-1:]
        exit_price_condition = data_dict["unitInfo"]["exitAllPrice"]
        entry_is_more = entry_data["isMore"]
        sl_is_more = not entry_is_more
        sl_action = ""
        if entry_action == "BUY":
            sl_action = "SELL"
        elif entry_action == "SELL":
            sl_action = "BUY"
        entry_order = self.create_order(instrument=instrument,
                                        order_id=self.ib.client.getReqId(),
                                        action=entry_action,
                                        order_type=order_type,
                                        total_quantity=total_quantity,
                                        transmit=False,
                                        price_condition=entry_price_condition,
                                        is_more=entry_is_more,
                                        order_ref=entry_order_ref)
        sl_order = self.create_order(instrument=instrument,
                                     order_id=self.ib.client.getReqId(),
                                     action=sl_action,
                                     order_type=order_type,
                                     total_quantity=total_quantity,
                                     transmit=False,
                                     price_condition=sl_price_condition,
                                     is_more=sl_is_more,
                                     order_ref=sl_order_ref,
                                     parent_id=entry_order.orderId)
        exit_order = self.create_order(instrument=instrument,
                                       order_id=self.ib.client.getReqId(),
                                       action=sl_action,
                                       order_type=order_type,
                                       total_quantity=total_quantity,
                                       transmit=True,
                                       price_condition=exit_price_condition,
                                       is_more=sl_is_more,
                                       order_ref=exit_order_ref,
                                       parent_id=entry_order.orderId)
        orders = {
                    "entry_order": entry_order,
                    "sl_order": sl_order,
                    "exit_order": exit_order
                }
        return orders

####################################################
    def connect(self):
        """Connect to Interactive Brokers TWS"""

        self.log('Connecting to Interactive Brokers TWS...')
        try:
            ib = IB()
            ib.connect('127.0.0.1', 7497, clientId=0)
            ib.reqAutoOpenOrders(True)
            # Requesting manual pending orders doesn't work with this:
            # ib.connect('127.0.0.1', 7497, clientId=1)
            self.log('Connected')
            self.log()
            return ib
        except:
            self.log('Error in connecting to TWS!! Exiting...')
            self.log(sys.exc_info()[0])
            exit(-1)

#####################################################
    def log(self, msg=""):
        """Add log to output file"""
        self.logger.info(msg)
        print(msg)

#####################################################
    def get_open_trades(self, instrument):
        """Returns the number of unfilled trades open for a currency"""
        orders = []
        self.ib.sleep(1)
        for t in self.ib.openTrades():
            if t.contract.localSymbol == instrument.localSymbol:
                orders.append(t)
        order_count = len(orders)
        self.log('Currently in {} open orders for instrument {}.'
                 .format(order_count, instrument.localSymbol))
        return orders

#####################################################
    def get_filled_executions(self, instrument):
        """Returns the number of filled executions in past week"""
        fills = []
        self.ib.sleep(1)
        for f in self.ib.reqExecutions():
            if f.contract.localSymbol == instrument.localSymbol:
                self.log('Found trade with symbol {}: {}'.format(f.contract.localSymbol, f.execution.avgPrice))
                fills.append(f)
        fill_count = len(fills)
        self.log('Currently in {} filled trades for instrument {}.'
                 .format(fill_count, instrument.localSymbol))
        return fills

#####################################################
    def add_instrument(self, instrument_type, ticker,
                       symbol, currency, exchange='IDEALPRO'):
        """Adds instrument as an IB contract to instruments list"""
        self.log("Adding instrument {}".format(ticker))

        if instrument_type == 'Forex':
            instrument = Forex(ticker, exchange=exchange,
                               symbol=symbol, currency=currency)
        else:
            raise ValueError(
                       "Invalid instrument type: {}".format(instrument_type))

        self.ib.qualifyContracts(instrument)
        self.instruments.append(instrument)

#####################################################
    def get_available_funds(self):
        """Returns available funds in USD"""
        account_values = self.ib.accountValues()
        available_funds = 0
        i = 0
        for value in account_values:
            if account_values[i].tag == 'AvailableFunds':
                available_funds = float(account_values[i].value)
                break
            i += 1
        return available_funds

#####################################################
    def get_cash_balance(self, instrument):
        """Returns current position for currency pair in units"""
        account_values = self.ib.accountValues()
        cash_balance = 0
        i = 0
        for value in account_values:
            if account_values[i].tag == 'CashBalance' and \
               account_values[i].currency == instrument.localSymbol[0:3]:
                cash_balance = float(account_values[i].value)
                break
            i += 1
        return cash_balance

#####################################################
    def get_base_exchange(self, instrument):
        """Get the exchange rate between currency and base.
        If trading:
        GBP.JPY, will return GBP.USD
        AUD.CAD, will return AUD.USD
        EUR.USD, will return EUR.USD"""
        assert (instrument.localSymbol in ['GBP.JPY', 'AUD.CAD', 'EUR.USD']), \
               'Invalid Currency!'

        base = ""

        for v in self.ib.accountValues():
            if v.tag == 'AvailableFunds':
                base = v.currency

        symbol = instrument.localSymbol[-3:] if instrument.localSymbol[-3:] == 'JPY' else instrument.localSymbol[-7:-4]

        if symbol == 'JPY':
            pair = base + symbol
            self.log("Getting current exchange rate for pair {}".format(pair))
            ticker = self.ib.reqMktData(contract=Forex(pair=pair,
                                                       symbol=base,
                                                       currency=symbol))
            self.ib.sleep(1)
            self.log("1 {} = {} USD".format(symbol, 1 / ticker.marketPrice()))
            return 1 / ticker.marketPrice()
        else:
            pair = symbol + base
            self.log("Getting current exchange rate for pair {}".format(pair))
            ticker = self.ib.reqMktData(contract=Forex(pair=pair,
                                                       symbol=symbol,
                                                       currency=base))
            self.ib.sleep(1)
            self.log("1 {} = {} USD".format(symbol, ticker.marketPrice()))
            return ticker.marketPrice()

#####################################################
    def set_position_size(self, instrument):
        """Sets position size in BASE based on available funds and volitility"""
        position_size = None
        unit_info = (self.get_data_from_json()
                     [instrument.localSymbol]["unitInfo"])
        max_unit_size = unit_info["maxUnitSize"]
        current_unit_size = unit_info["currentUnitSize"]
        base_exchange = unit_info["baseExchange"]
        sl_size = unit_info["slSize"]
        local_symbol = instrument.localSymbol[:3] + instrument.localSymbol[4:]
        ticker = self.ib.reqMktData(contract=Forex(pair=local_symbol,
                                                   symbol=local_symbol[:3],
                                                   currency=local_symbol[3:]))
        self.ib.sleep(1)
        exchange_rate = ticker.marketPrice()
        position_size = round(min((max_unit_size - current_unit_size),
                              (self.get_max_equity_at_risk()
                              / base_exchange
                              / sl_size
                              / 4)))
        return position_size

#####################################################
    def get_atr_multiple(self, instrument, multiplier=0.5):
        """Sets absolute value of SL equal to 1/2 ATR"""
        indicators = self.get_indicators(instrument)
        volatility = indicators['atr'][(indicators.axes[0].stop - 1)]
        sl_size = self.adjust_for_price_increments(instrument,
                                                   multiplier * volatility)
        # self.log('Current ATR={}, sl={}'.format(volatility, sl_size))
        return sl_size

#####################################################
    def adjust_for_price_increments(self, instrument, value):
        """Adjust given value for instrument's allowed price increments."""
        increment = None
        if instrument.localSymbol == 'EUR.USD':
            increment = 0.00005
        elif instrument.localSymbol == 'GBP.JPY':
            increment = 0.005
        elif instrument.localSymbol == 'AUD.CAD':
            increment = 0.00005
        else:
            self.log('Invalid pair! Cannot calculate SL!')
            return None
        value = increment * round(value / increment)
        return value

#####################################################
    def create_order(self,
                     instrument,
                     order_id,
                     action,
                     order_type,
                     tif="GTC",
                     total_quantity=0,
                     transmit=False,
                     *args, **kwargs):
        """Places order with IBKR given relevant info.
        kwargs:
        bool is_more - True if price condition is >, False if <
        bool price_condition - True if there is a price condition, else False
        order_ref - can manually input order reference number
        parent_id - can manually input parent order ID"""
        is_more = kwargs.get('is_more', "ERROR")
        price_condition = kwargs.get('price_condition', "ERROR")
        parent_id = kwargs.get('parent_id', "ERROR")
        order_ref = kwargs.get('order_ref', "ERROR")

        order = Order()
        order.orderId = order_id
        order.action = action
        order.orderType = order_type
        order.totalQuantity = total_quantity
        order.transmit = transmit
        order.tif = tif
        if parent_id != "ERROR":
            order.parentId = parent_id

        if order_ref != "ERROR":
            order.orderRef = order_ref

        if price_condition != "ERROR" and is_more != "ERROR":
            order.conditions = [PriceCondition(conId = instrument.conId,
                                               exch='IDEALPRO',
                                               isMore=is_more,
                                               price=price_condition)]

        return order

#####################################################
    def get_indicators(self, instrument):
        """Returns 55 & 20 donchian channels for instrument"""
        bars = self.ib.reqHistoricalData(contract=instrument,
                                         endDateTime='',
                                         durationStr='6 M',
                                         barSizeSetting='1 day',
                                         whatToShow='MIDPOINT',
                                         useRTH=True)
        df = pd.DataFrame(bars)
        del df['volume']
        del df['barCount']
        del df['average']
        atr = pd.DataFrame(ta.atr(high=df['high'],
                           low=df['low'],
                           close=df['close'],
                           length=20))
        long_donchian = pd.DataFrame(ta.donchian(high=df['high'],
                                                 low=df['low'],
                                                 upper_length=55,
                                                 lower_length=55))
        short_donchian = pd.DataFrame(ta.donchian(high=df['high'],
                                                  low=df['low'],
                                                  upper_length=20,
                                                  lower_length=20))
        df = pd.concat([df, atr, long_donchian, short_donchian],
                       axis=1,
                       join="outer")
        df.columns.values[5] = 'atr'
        df.columns.values[6] = 'long_dcl'
        df.columns.values[7] = 'long_dcm'
        df.columns.values[8] = 'long_dcu'
        df.columns.values[9] = 'short_dcl'
        df.columns.values[10] = 'short_dcm'
        df.columns.values[11] = 'short_dcu'
        # self.log(df.tail())
        return df

#####################################################
# MAIN PROGRAMME:
if __name__ == '__main__':
    # Create algo object
    algo = IBAlgoStrategy()

    # Add instruments to trade
    algo.add_instrument('Forex', ticker='GBPJPY', symbol='GBP', currency='JPY')
    algo.add_instrument('Forex', ticker='EURUSD', symbol='EUR', currency='USD')
    algo.add_instrument('Forex', ticker='AUDCAD', symbol='AUD', currency='CAD')

    # Run for the day
    algo.run()