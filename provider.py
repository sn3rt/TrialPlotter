# -*- coding: utf-8 -*-
from qgis.core import QgsProcessingProvider


class TrialPlotterProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        from .algorithms.trial_plots_polygons_csv import TrialPlotsPolygonsCSVAlgorithm
        self.addAlgorithm(TrialPlotsPolygonsCSVAlgorithm())

    def id(self):
        return "wur_trialplotter"

    def name(self):
        return "TrialPlotter"

    def longName(self):
        return "TrialPlotter"

