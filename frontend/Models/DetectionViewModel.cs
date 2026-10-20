using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    // Shared shape for APD Detection and Vehicle Violation — the two
    // original pages (apd.html/vehicle.html) are near-identical, differing
    // only in category filter, accent color and copy.
    public class CategoryStats
    {
        public int TotalToday;
        public int OpenCases;
    }

    public class DetectionViewModel
    {
        public string Category; // "apd" | "vehicle"
        public string PageTitle;
        public string PageDescription;
        public string AccentColor; // "#B57712" (apd) | "#B8021F" (vehicle)
        public List<Camera> Cameras;
        public List<Violation> Violations;
        public int StatCams;
        public int? StatToday;
        public int? StatOpen;
    }
}
