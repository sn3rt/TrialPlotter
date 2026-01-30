# -*- coding: utf-8 -*-
import os
import inspect
import processing

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import Qgis, QgsMessageLog, QgsApplication

from .provider import TrialPlotterProvider


class TrialPlotterPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.provider = None

    def initGui(self):
        current_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
        icon_path = os.path.join(current_dir, "icons", "trialplotter.png")

        self.action = QAction(QIcon(icon_path), "TrialPlotter", self.iface.mainWindow())
        self.action.setWhatsThis("Generate trial plot polygons + CSV from 2/3 RTK points")
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu("TrialPlotter", self.action)

        # Register processing provider (so algorithm becomes available by ID)
        self.provider = TrialPlotterProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu("TrialPlotter", self.action)
            self.action = None

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        try:
            alg_id = "wur_trialplotter:trialplotter_algorithm"
            processing.execAlgorithmDialog(alg_id, {})
        except Exception as e:
            QgsMessageLog.logMessage(str(e), "TrialPlotter", Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "TrialPlotter", f"Could not open algorithm dialog:\n{e}")
