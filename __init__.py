# -*- coding: utf-8 -*-
from .trialplotter_plugin import TrialPlotterPlugin

def classFactory(iface):
    return TrialPlotterPlugin(iface)

