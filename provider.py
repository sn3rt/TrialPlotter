# -*- coding: utf-8 -*-
from qgis.core import QgsProcessingProvider


class TrialPlotterProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        from .algorithms.trialplotter_algorithm import TrialPlotterAlgorithm
        self.addAlgorithm(TrialPlotterAlgorithm())

    def id(self):
        return "wur_trialplotter"

    def name(self):
        return "TrialPlotter"

    def longName(self):
        return "TrialPlotter"

