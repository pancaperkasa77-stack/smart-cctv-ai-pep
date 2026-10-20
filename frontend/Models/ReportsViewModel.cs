using System;
using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    public class CameraReportRow
    {
        public string CameraName;
        public string Location;
        public string Category;
        public int Count;
    }

    public class ReportsViewModel
    {
        public DateTime Start;
        public DateTime End;
        public string QuickActive; // "7" | "30" | null (custom range)
        public string RangeError;

        public string RangeLabel;
        public int Total;
        public double AvgPerDay;
        public int CaseTotal;
        public double? CompletionRate; // 0..1
        public double? AvgResponseMinutes;

        public int ApdTotal;
        public int VehicleTotal;
        public int ApdPct;
        public int VehiclePct;

        public List<DailyCount> Daily = new List<DailyCount>();
        public List<CameraReportRow> ByCamera = new List<CameraReportRow>();
    }
}
