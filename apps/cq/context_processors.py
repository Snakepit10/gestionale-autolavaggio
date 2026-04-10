from datetime import date


def oggi(request):
    return {'today': date.today()}
