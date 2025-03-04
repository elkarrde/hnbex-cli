import tempfile

from datetime import timedelta
from decimal import Decimal
from importlib.resources import files
from os.path import realpath, join, dirname
from subprocess import call

from hnbex.api import fetch_daily, fetch_range
from hnbex.output import print_out, print_conversion, print_err, print_table


HRK_EUR_RATE = Decimal(7.5345)
allowed_currencies = ['EUR', 'HRK']


class CommandError(Exception):
    pass


def wrap(tag, text):
    return f"<{tag}>{text}</{tag}>"


def daily(date, **kwargs):
    rates = fetch_daily(date)

    print_out(f"HNB exchange rates on <yellow>{date:%Y-%m-%d}</yellow>")
    print_out()

    if len(rates) == 0:
        print_out("No data found for given date")
        return

    def spread(rate):
        buy = rate.buying_rate
        sell = rate.selling_rate
        if sell > 0:
            diff = (sell - buy) / sell
            return f"{diff:.2%}"
        return ""

    data = [(
        wrap("yellow", rate.currency_code),
        rate.buying_rate,
        rate.median_rate,
        rate.selling_rate,
        spread(rate)
    ) for rate in rates]

    headers = ["Currency", "Buying", "Median", "Selling", "Spread"]
    print_table(headers, data)


def _range_dates(start, end, days):
    if not start:
        start = end - timedelta(days=days - 1)

    if start > end:
        raise CommandError("Start date cannot be greater than end date")

    return start, end


def _diff(old, new):
    if not old:
        return ""

    diff = (new - old) / old
    formatted = f"{diff:.2%}"

    if diff < 0:
        return f"<red>{formatted}</red>"

    if diff > 0:
        return f"<green>+{formatted}</green>"

    return f" {formatted}"


def _range_lines(rates):
    prev_median = None
    diff_median = None

    for rate in rates:
        median = rate.median_rate
        diff_median = _diff(prev_median, median)

        yield(
            wrap("yellow", rate.date),
            rate.buying_rate,
            rate.median_rate,
            rate.selling_rate,
            diff_median,
        )

        prev_median = rate.median_rate


def range(currency, start, end, days, **kwargs):
    start_date, end_date = _range_dates(start, end, days)
    rates = fetch_range(currency, start_date, end_date)

    print_out(
        f"HNB exchange rates for <yellow>{currency}</yellow>",
        f"from <yellow>{start_date:%Y-%m-%d}</yellow>",
        f"to <yellow>{end_date:%Y-%m-%d}</yellow>\n"
    )

    if not rates:
        print_out("No data found for given date range")
        return

    headers = ['Date', 'Buying', 'Median', 'Selling', 'Diff']
    print_table(headers, _range_lines(rates))


def _get_rate(date, currency):
    rates = fetch_daily(date, currency_code=currency)

    if rates:
        return rates[0]

    raise CommandError(f"Exchange rate for {currency} not found")


def convert(amount, source_currency, target_currency, date, precision, value_only, show_euro, **kwargs):
    if precision < 0:
        raise CommandError("Precision must be greater than 0.")

    if source_currency not in allowed_currencies and target_currency not in allowed_currencies:
        raise CommandError("Either source or target currency must be EUR or HRK.")

    if source_currency == target_currency:
        raise CommandError("Source and target currency are the same.")

    if source_currency in allowed_currencies and target_currency in allowed_currencies:
        return convert_eurhrk(amount, source_currency, target_currency, date, precision, value_only, show_euro)
    elif source_currency == 'HRK' or target_currency == 'HRK':
        return convert_hrk(amount, source_currency, target_currency, date, precision, value_only, show_euro)
    else:
        if source_currency == 'EUR':
            rate = _get_rate(date, target_currency)
            result = amount * rate.median_rate
        else:
            rate = _get_rate(date, source_currency)
            result = amount / rate.median_rate

    exponent = Decimal(10) ** -precision
    result = result.quantize(exponent)

    print_conversion(amount, result, source_currency, target_currency, rate, value_only=value_only)


def convert_hrk(amount, source_currency, target_currency, date, precision, value_only, show_euro, **kwargs):
    if source_currency == 'HRK' and target_currency != 'EUR':
        rate = _get_rate(date, target_currency)
        result = amount / HRK_EUR_RATE * rate.median_rate
        result_eur = amount / HRK_EUR_RATE
    elif source_currency != 'EUR' and target_currency == 'HRK':
        rate = _get_rate(date, source_currency)
        result = amount / rate.median_rate * HRK_EUR_RATE
        result_eur = amount / rate.median_rate

    exponent = Decimal(10) ** -precision
    result = result.quantize(exponent)

    hrk_exp = Decimal(10) ** -4
    hrk_rate = HRK_EUR_RATE.quantize(hrk_exp)
    result_eur = result_eur.quantize(exponent)

    print_conversion(amount, result, source_currency, target_currency, rate, value_only=value_only, result_eur=result_eur, hrk_rate=hrk_rate, show_euro=show_euro)


def convert_eurhrk(amount, source_currency, target_currency, date, precision, value_only, show_euro, **kwargs):
    if source_currency == 'HRK' and target_currency == 'EUR':
        rate = _get_rate(date, target_currency)
        result = amount / rate.median_rate
    elif source_currency == 'EUR' and target_currency == 'HRK':
        rate = _get_rate(date, source_currency)
        result = amount * rate.median_rate

    exponent = Decimal(10) ** -precision
    result = result.quantize(exponent)

    print_conversion(amount, result, source_currency, target_currency, rate, value_only=value_only, show_euro=show_euro)


def abspath(path):
    return join(realpath(dirname(__file__)), path)


def chart(currency, template, start, end, days, **kwargs):
    start_date, end_date = _range_dates(start, end, days)
    rates = fetch_range(currency, start_date, end_date)
    plot_data = "\n".join([f"{rate.date} {rate.median_rate}" for rate in rates])

    script_template = (
        files("hnbex.templates")
        .joinpath("{}.gnuplot".format(template))
        .read_text()
    )

    with tempfile.NamedTemporaryFile() as script_file:
        with tempfile.NamedTemporaryFile() as data_file:
            script = script_template.format(
                currency=currency,
                start_date=start_date,
                end_date=end_date,
                data_file=data_file.name,
            )

            script_file.write(script.encode('utf-8'))
            script_file.flush()

            data_file.write(plot_data.encode('utf-8'))
            data_file.flush()

            _plot(script_file)


def _plot(script_file):
    try:
        call(['gnuplot', '-c', script_file.name, '-p'])
    except FileNotFoundError as ex:
        print_err(ex)
        raise CommandError("Charting failed. Do you have gnuplot installed?")
