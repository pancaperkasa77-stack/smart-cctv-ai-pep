using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    public class TopLocationRow
    {
        public string CameraName;
        public string Location;
        public int Count;
        public string Category;
    }

    public class DashboardViewModel
    {
        public int TotalCameras;
        public int TotalViolations;
        public int OpenCases;
        public int Last24h;
        public int ApdCameraCount;
        public int ApdViolationCount;
        public int VehicleCameraCount;
        public int VehicleViolationCount;
        public List<DailyCount> Daily;
        public List<TopLocationRow> TopLocations;
        public List<Violation> Feed;
    }
}
