using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    public class RetentionViewModel
    {
        public RetentionViewModel()
        {
            Cameras = new List<Camera>();
            MaxRecords = "1000";
        }

        public MvcSmartCctv.Services.PlaybackSettings Playback { get; set; }
        public List<Camera> Cameras { get; set; }
        public bool Enabled { get; set; }
        public string MaxRecords { get; set; }
        public RetentionSummary Summary { get; set; }
        public RetentionSummary Preview { get; set; }
        public string Error { get; set; }
        public string CameraError { get; set; }
        public string Notice { get; set; }
    }

    // Wire names deliberately match the retention API contract.
    public class RetentionSummary
    {
        public bool enabled { get; set; }
        public int max_records { get; set; }
        public long protected_open_cases { get; set; }
        public long total_records { get; set; }
        public long eligible_for_deletion { get; set; }
        public long pending_file_deletions { get; set; }
        public string last_run_at { get; set; }
        public string last_error { get; set; }
        public long version { get; set; }
        public string preview_token { get; set; }
    }
}
