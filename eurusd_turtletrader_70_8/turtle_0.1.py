# MAIN LOOP:
# CONNECT TO TWS
# GET CURRENT POSITION DETAILS
# IF IN A LONG POSITION:
#   IF SL HAS BEEN HIT OR EXIT CONDITION (8 DAY LOW) HAS BEEN MET IN PAST 24 HOURS:
#       CASH OUT OF ALL POSITIONS
#       IF ENTRY POSITION HAS BEEN MET IN PAST 24 HOURS:
#           ENTER POSITION
#           EXIT PROGRAMME
#   ELIF IN <4 POSITIONS AND COMPOUND SIGNAL HAS BEEN MET IN PAST 24 HOURS:
#       ENTER COMPOUND POSITION
#       EXIT PROGRAMME
# ELIF IN A SHORT POSITION:
#   IF SL HAS BEEN HIT OR EXIT CONDITION (8 DAY HIGH) HAS BEEN MET IN PAST 24 HOURS:
#       CASH OUT OF ALL POSITIONS
#       IF ENTRY POSITION HAS BEEN MET IN PAST 24 HOURS:
#           ENTER POSITION
#           EXIT PROGRAMME
#   ELIF IN <4 POSITIONS AND COMPOUND SIGNAL HAS BEEN MET IN PAST 24 HOURS:
#       ENTER COMPOUND POSITION
#       EXIT PROGRAMME
# ELSE:
#   IF ENTRY POSITION HAS BEEN MET IN PAST 24 HOURS
#   ENTER POSITION
#   EXIT PROGRAMME

#####################################################
# IMPORT REQUIRED LIBRARIES
import datetime
from ib_insync import *
from ibapi import *
import logging
import pytz
import sys
import pandas as pd
import pandas_ta as ta

#####################################################
# Algorithmic strategy class for interactive brokers:
class IBAlgoStrategy(object):
    """
    Algorithmic trading strategy for Interactive Brokers
    """

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
            indicators = self.get_indicators(instrument)
            positions = self.get_positions(instrument.localSymbol)
            position_count = len(positions)
            self.log('Currently in {} positions for instrument {}.'
                     .format(position_count, instrument.localSymbol))
            if position_count == 0:
                self.place_initial_entry_orders(instrument, indicators)
            elif position_count < 4:
                # Place compound long orders
                if self.is_long(instrument.localSymbol):
                    i = 4 - position_count
                    while i <= 4:
                        self.place_compound_long_order(instrument, indicators, "NULL")
                        i += 1
                # Place compound short orders
                elif not self.is_long(instrument.localSymbol):
                    i = 4 - position_count
                    while i <= 4:
                        self.place_compound_short_order(instrument, indicators, "NULL")
                        i += 1
        self.log(self.ib.positions)

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
    def get_positions(self, localSymbol):
        """Returns the current quantity held for instrument"""
        positions = []
        self.ib.sleep(1)
        for position in self.ib.reqCompletedOrders(apiOnly=False):
            if position.contract.localSymbol == localSymbol:
                if position.orderStatus.status == "Cancelled":
                    pass
                else:
                    positions.append(position)
                    # self.log('Found position for instrument {}: {}'
                    #          .format(localSymbol, position))
        return positions

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
        # self.log('Available funds (USD): {}'.format(available_funds))
        return available_funds

#####################################################
    def set_position_size(self, instrument, indicators, sl_size):
        """Sets position size in USD based on available funds and volitility"""
        position_size = 0

        # Get position size in USD
        available_funds = self.get_available_funds()
        equity_at_risk = available_funds * 0.02
        size_in_usd = int(round(equity_at_risk / sl_size, 2))

        # Get position size as units of symbol
        if instrument.localSymbol == 'EUR.USD':
            position_size = round(equity_at_risk / sl_size)
        elif instrument.localSymbol == 'GBP.JPY':
            ticker = self.ib.reqMktData(contract=Forex(pair='USDJPY',
                                                       symbol='USD',
                                                       currency='JPY'))
            self.ib.sleep(1)
            position_size = round(ticker.marketPrice()
                                  * equity_at_risk
                                  / sl_size)
        elif instrument.localSymbol == 'AUD.CAD':
            ticker = self.ib.reqMktData(contract=Forex(pair='USDCAD',
                                                       symbol='USD',
                                                       currency='CAD'))
            self.ib.sleep(1)
            position_size = round(ticker.marketPrice()
                                  * equity_at_risk
                                  / sl_size)
        else:
            self.log('Invalid instrument {}! Could not calculate position size.'
                     .format(instrument.localSymbol))
            return None
        return position_size

#####################################################
    def set_sl_size(self, instrument, indicators):
        """Sets absolute value of SL equal to 2x ATR"""
        indicators = self.get_indicators(instrument)
        volatility = indicators['atr'][(indicators.axes[0].stop - 1)]
        sl_size = self.adjust_for_price_increments(instrument, 2 * volatility)
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
    def place_initial_entry_orders(self, instrument, indicators):
        """Long initial entry"""
        sl_size = self.set_sl_size(instrument, indicators)
        total_quantity = self.set_position_size(instrument,
                                                indicators,
                                                sl_size)
        long_term_high = self.adjust_for_price_increments(instrument,
                                                          indicators
                                                          ['long_dcu']
                                                          [(indicators.axes[0]
                                                            .stop - 1)])
        long_term_low = self.adjust_for_price_increments(instrument,
                                                         indicators
                                                         ['long_dcl']
                                                         [(indicators.axes[0]
                                                           .stop - 1)])
        long_exit_condition = self.adjust_for_price_increments(instrument,
                                                               indicators
                                                               ['short_dcl']
                                                               [(indicators
                                                                .axes[0]
                                                                .stop - 1)])
        short_exit_condition = self.adjust_for_price_increments(instrument,
                                                                indicators
                                                                ['short_dcu']
                                                                [(indicators
                                                                 .axes[0]
                                                                 .stop - 1)])
        long_bracket = self.mkt_order_adj_sl_conditions(self.ib.client
                                                            .getReqId(),
                                                        self.ib.client
                                                            .getReqId(),
                                                        self.ib.client
                                                            .getReqId(),
                                                        "BUY",
                                                        total_quantity,
                                                        instrument,
                                                        long_term_high,
                                                        long_exit_condition,
                                                        True,
                                                        sl_size)
        short_bracket = self.mkt_order_adj_sl_conditions(self.ib.client
                                                             .getReqId(),
                                                         self.ib.client
                                                             .getReqId(),
                                                         self.ib.client
                                                             .getReqId(),
                                                         "SELL",
                                                         total_quantity,
                                                         instrument,
                                                         long_term_low,
                                                         short_exit_condition,
                                                         False,
                                                         sl_size)
        # Add long and short entries together
        brackets = []
        for o in long_bracket:
            brackets.append(o)
        for o in short_bracket:
            brackets.append(o)
        oca = self.ib.oneCancelsAll(orders=brackets,
                                    ocaGroup="OCA_"
                                    + str(instrument.localSymbol)
                                    + str(self.ib.client.getReqId()),
                                    ocaType=1)
        for o in oca:
            self.ib.placeOrder(instrument, o)
            #self.log("Placed order {}".format(o))
            self.ib.sleep(2)
        # self.log("Current Orders Number = {}"
        #          .format(len(self.ib.openOrders())))

#####################################################
    def place_compound_long_order(self, instrument, indicators, parent):
        """Place a single long compound order"""
        sl_size = self.set_sl_size(instrument, indicators)
        total_quantity = self.set_position_size(instrument,
                                                indicators,
                                                sl_size)
        price_condition = parent.orderStatus.avgFillPrice + 0.5 * sl_size
        bracket = self.mkt_order_adj_sl_conditions(parent.orderId,
                                                   self.ib.client.getReqId(),
                                                   total_quantity,
                                                   instrument,
                                                   price_condition,
                                                   sl_size)
        for o in bracket:
            self.ib.placeOrder(instrument, o)
            #self.log("Placed order {}".format(o))
            self.ib.sleep(2)

#####################################################
    def place_compound_short_order(self, instrument, indicators, parent):
        """Place a single short compound order"""
        pass

#####################################################
    def met_long_exit_condition(self):
        return False

#####################################################
    def met_short_exit_condition(self):
        return False

#####################################################
    def close_position(self):
        pass

#####################################################
    def mkt_order_adj_sl_conditions(self,
                                    parentOrderId,
                                    childOrderId,
                                    exitOrderId,
                                    action,
                                    totalQuantity,
                                    instrument,
                                    price_condition,
                                    exit_price_condition,
                                    is_more,
                                    sl_size):
        """Place a market order with price condition, adjustable SL, and
        additional market order exit condition based on donchian channels"""

        # Parent order:
        parent = Order()
        parent.orderId = parentOrderId
        parent.action = action
        parent.orderType = "MKT"
        parent.totalQuantity = totalQuantity
        isMore = True if parent.action == "BUY" else False
        parent.conditions = [PriceCondition(conId=instrument.conId,
                                            exch='IDEALPRO',
                                            isMore=is_more,
                                            price=price_condition)]
        parent.transmit = False

        # Adjusted stop order:
        # (adjust to breakeven if price moves 1x ATR in favor of trade)
        stopLoss = Order()
        stopLoss.orderId = childOrderId
        stopLoss.parentId = parentOrderId
        stopLoss.action = "SELL" if action == "BUY" else "BUY"
        stopLoss.orderType = "STP"
        stopLoss.totalQuantity = totalQuantity
        stopLoss.adjustedOrderType = "STP"
        stopLoss.adjustedStopPrice = price_condition
        stopLoss.tif = "GTC"
        if stopLoss.action == "BUY":
            # Trigger price for adj. SL:
            stopLoss.triggerPrice = price_condition - sl_size
            # SL price for unadj. SL:
            stopLoss.auxPrice = price_condition + sl_size
        else:
            # Trigger price for adj. SL:
            stopLoss.triggerPrice = price_condition + sl_size
            # SL price for unadj. SL:
            stopLoss.auxPrice = price_condition - sl_size
        stopLoss.transmit = True

        # Exit condition order (short term donchian):
        exit_order = Order()
        exit_order.orderId = exitOrderId
        exit_order.parentId = parentOrderId
        exit_order.action = "SELL" if action == "BUY" else "BUY"
        exit_order.orderType = "MKT"
        exit_order.totalQuantity = totalQuantity
        exit_order.tif = "GTC"
        exit_is_more = not isMore
        exit_order.conditions = [PriceCondition(conId=instrument.conId,
                                                exch='IDEALPRO',
                                                isMore=exit_is_more,
                                                price=exit_price_condition)]

        # Group the two exit conditions (SL and short term donchian) into OCA:
        self.ib.oneCancelsAll([exit_order, stopLoss],
                              ocaGroup="OCA_"
                              + str(instrument.localSymbol)
                              + str(self.ib.client.getReqId()),
                              ocaType=1)
        return [parent, stopLoss, exit_order]

#####################################################
    def get_open_trades(self, instrument):
        """Returns a dataframe of all open trades"""
        self.ib.sleep(1)
        trades = []
        for trade in self.ib.openTrades():
            if trade.contract.localSymbol == instrument.localSymbol:
                trades.append(trade.dict())
        df = pd.DataFrame(trades)
        return df

#####################################################
    def get_indicators(self, instrument):
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
                                                 upper_length=70,
                                                 lower_length=70))
        short_donchian = pd.DataFrame(ta.donchian(high=df['high'],
                                                  low=df['low'],
                                                  upper_length=8,
                                                  lower_length=8))
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
    def is_long(self, localSymbol):
        for position in self.ib.positions():
            if position.contract.localSymbol == localSymbol:
                if position.position > 0:
                    return True
        return False

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
