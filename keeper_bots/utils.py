# Symbol names
UPDATE_TIME = "UPDATE TIME"
CREATE_TIME = "CREATE TIME"
INSTRUMENT = "INSTRUMENT"
MARKET = "MARKET"
ORDER_TYPE = "ORDER TYPE"
ORDER_STATE = "ORDER STATE"
ORDER_BOOK_SIDE = "ORDER BOOK SIDE"

# Instruments
SPOT = "SPOT"
MARGIN = "MARGIN"
SWAP = "SWAP"
FUTURES = "FUTURES"
OPTION = "OPTION"

# Order types
MARKET_ORDER = "MARKET"
LIMIT_ORDER = "LIMIT"
POST_ONLY_ORDER = "POST ONLY"
FILL_OR_KILL_ORDER = "FILL OR KILL"
IMMEDIATE_OR_CANCEL_ORDER = "IMMEDIATE OR CANCEL"

# Order state
CANCELED = "CANCELED"
LIVE = "LIVE"
PARTIALLY_FILLED = "PARTIALLY FILLED"
FILLED = "FILLED"
AUTO_CANCELED = "AUTO-CANCELED"

# Order book side
BID = "BID"
ASK = "ASK"


# Internal to external symbol
def unparse_symbol(exchange, instrument, market):

    if exchange == "OKX":

        if instrument == SPOT:
            return ["SPOT", market]
        else:
            raise ValueError(f"Do not know how to unparse instrument {instrument} for exchange {exchange}")

    else:
        raise ValueError(f"Do not know how to unparse for exchange {exchange}")


# External to internal symbol
def parse_symbol(exchange, symbol_name, symbol=None):

    if exchange == "OKX":

        if symbol is None:

            if symbol_name == "uTime":
                return UPDATE_TIME
            elif symbol_name == "cTime":
                return CREATE_TIME
            elif symbol_name == "instType":
                return INSTRUMENT
            elif symbol_name == "instId":
                return MARKET
            elif symbol_name == "ordType":
                return ORDER_TYPE
            elif symbol_name == "state":
                return ORDER_STATE
            elif symbol_name == "side":
                return ORDER_BOOK_SIDE
            else:
                raise ValueError(f"Symbol name {symbol_name} not known for exchange {exchange}")

        else:

            if symbol_name == "uTime" or symbol_name == "cTime":

                return symbol # OKX timestamps are already Unix time in milliseconds

            elif symbol_name == "instType":

                if symbol == "SPOT":
                    return SPOT
                elif symbol == "MARGIN":
                    return MARGIN
                elif symbol == "SWAP":
                    return SWAP
                elif symbol == "FUTURES":
                    return FUTURES
                elif symbol == "OPTION":
                    return OPTION
                else:
                    raise ValueError(f"Unknown symbol {symbol} for symbol name {symbol_name} and exchange {exchange}")

            elif symbol_name == "instId":
                return symbol # TODO: check whether reformatting is required

            elif symbol_name == "ordType":

                if symbol == "market":
                    return MARKET_ORDER
                elif symbol == "limit":
                    return LIMIT_ORDER
                elif symbol == "post_only":
                    return POST_ONLY_ORDER
                elif symbol == "fok":
                    return FILL_OR_KILL_ORDER
                elif symbol == "ioc":
                    return IMMEDIATE_OR_CANCEL_ORDER
                else:
                    raise ValueError(f"Unknown symbol {symbol} for symbol name {symbol_name} and exchange {exchange}")

            elif symbol_name == "state":

                if symbol == "canceled":
                    return CANCELED
                elif symbol == "live":
                    return LIVE
                elif symbol == "partially_filled":
                    return PARTIALLY_FILLED
                elif symbol == "filled":
                    return FILLED
                elif symbol == "mmp_canceled":
                    return AUTO_CANCELED
                else:
                    raise ValueError(f"Unknown symbol {symbol} for symbol name {symbol_name} and exchange {exchange}")

            elif symbol_name == "side":

                if symbol == "buy":
                    return BID
                elif symbol == "sell":
                    return ASK
                else:
                    raise ValueError(f"Unknown symbol {symbol} for symbol name {symbol_name} and exchange {exchange}")

            else:
                raise ValueError(f"Unknown symbol name {symbol_name} for exchange {exchange}")

    else:
        raise ValueError(f"Unknown exchange {exchange}")
