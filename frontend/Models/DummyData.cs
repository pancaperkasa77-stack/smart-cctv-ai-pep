using System;
using System.Collections.Generic;
using System.Linq;

namespace MvcSmartCctv.Models
{
    public class Camera
    {
        public int Id;
        public string Name;
        public string IpAddress;
        public string Location;
        public string Category; // "apd" | "vehicle"
        public string StreamPath; // null => "Belum ada stream" placeholder
    }

    public class Note
    {
        public string Author;
        public DateTime CreatedAt;
        public string Text;
    }

    public class Violation
    {
        public int Id;
        public int CameraId;
        public string CameraName;
        public string Location;
        public string Category; // "apd" | "vehicle"
        public string Label;
        public DateTime CreatedAt;
        public bool IsCase;
        public string Status; // "baru" | "diproses" | "selesai"
        public string AssignedTo;
        public bool HasSnapshot;
        public string SnapshotPath;
        public string Severity; // "critical" | "warning"
        public List<Note> Notes = new List<Note>();
    }

    public class Operator
    {
        public string Username;
        public string Name;
    }

    public class PlatformUser
    {
        public string Id;
        public string Username;
        public string FirstName;
        public string LastName;
        public string Email;
        public List<string> Roles;
        public bool Enabled;
        public string Password;
    }

    public class DailyCount
    {
        public DateTime Date;
        public int Apd;
        public int Vehicle;
    }

    // Single in-memory dummy dataset for the whole app — this is a visual
    // mockup only (no real backend/DB/API), so everything here is static
    // seed data regenerated fresh on every app-domain start.
    public static class DummyData
    {
        public static readonly List<Camera> Cameras = new List<Camera>
        {
            new Camera { Id = 1, Name = "CAM-01 Gerbang Utama", IpAddress = "192.168.56.101", Location = "Gerbang Utama", Category = "apd", StreamPath = "cam1" },
            new Camera { Id = 2, Name = "CAM-02 Area Produksi A", IpAddress = "192.168.56.102", Location = "Area Produksi A", Category = "apd", StreamPath = "cam2" },
            new Camera { Id = 3, Name = "CAM-03 Workshop", IpAddress = "192.168.56.103", Location = "Workshop", Category = "apd", StreamPath = null },
            new Camera { Id = 4, Name = "CAM-04 Pos Satpam", IpAddress = "192.168.56.104", Location = "Pos Satpam", Category = "vehicle", StreamPath = "cam4" },
            new Camera { Id = 5, Name = "CAM-05 Jalan Akses Tambang", IpAddress = "192.168.56.105", Location = "Jalan Akses Tambang", Category = "vehicle", StreamPath = "cam5" },
        };

        public static readonly List<Operator> Operators = new List<Operator>
        {
            new Operator { Username = "operator1", Name = "Budi Santoso" },
            new Operator { Username = "operator2", Name = "Siti Rahayu" },
            new Operator { Username = "supervisor1", Name = "Agus Wijaya" },
        };

        // Password is plaintext deliberately — this is an in-memory demo
        // dataset with no real backend, matching the original Keycloak
        // realm's shared demo password (see infra/keycloak/import/
        // realm-smart-cctv-ai.json) rather than inventing per-user secrets.
        public static readonly List<PlatformUser> Users = new List<PlatformUser>
        {
            new PlatformUser { Id = "u1", Username = "admin", FirstName = "Rina", LastName = "Kusuma", Email = "rina.kusuma@pertamina-ep.local", Roles = new List<string>{ "admin" }, Enabled = true, Password = "busDev123!" },
            new PlatformUser { Id = "u2", Username = "supervisor1", FirstName = "Agus", LastName = "Wijaya", Email = "agus.wijaya@pertamina-ep.local", Roles = new List<string>{ "supervisor" }, Enabled = true, Password = "busDev123!" },
            new PlatformUser { Id = "u3", Username = "operator1", FirstName = "Budi", LastName = "Santoso", Email = "budi.santoso@pertamina-ep.local", Roles = new List<string>{ "operator" }, Enabled = true, Password = "busDev123!" },
            new PlatformUser { Id = "u4", Username = "operator2", FirstName = "Siti", LastName = "Rahayu", Email = "siti.rahayu@pertamina-ep.local", Roles = new List<string>{ "operator" }, Enabled = false, Password = "busDev123!" },
        };

        public static PlatformUser FindUser(string username, string password)
        {
            return Users.FirstOrDefault(u => u.Username == username && u.Password == password && u.Enabled);
        }

        public static bool UsernameExists(string username)
        {
            return Users.Any(u => u.Username == username);
        }

        public static string NextUserId()
        {
            var maxN = Users.Select(u => int.Parse(u.Id.Substring(1))).DefaultIfEmpty(0).Max();
            return "u" + (maxN + 1);
        }

        public static readonly List<Violation> Violations = BuildViolations();

        private static List<Violation> BuildViolations()
        {
            var now = DateTime.Now;
            var list = new List<Violation>
            {
                new Violation { Id = 7, CameraId = 1, CameraName = "CAM-01 Gerbang Utama", Location = "Gerbang Utama", Category = "apd", Label = "Tidak memakai helm", CreatedAt = now.AddMinutes(-18), IsCase = true, Status = "diproses", AssignedTo = "Budi Santoso", HasSnapshot = true, Severity = "critical",
                    Notes = { new Note { Author = "Agus Wijaya", CreatedAt = now.AddMinutes(-15), Text = "Sudah dikonfirmasi ke supervisor lapangan." } } },
                new Violation { Id = 6, CameraId = 4, CameraName = "CAM-04 Pos Satpam", Location = "Pos Satpam", Category = "vehicle", Label = "Penumpang di bak kendaraan", CreatedAt = now.AddMinutes(-42), IsCase = true, Status = "baru", AssignedTo = null, HasSnapshot = true, Severity = "critical" },
                new Violation { Id = 5, CameraId = 2, CameraName = "CAM-02 Area Produksi A", Location = "Area Produksi A", Category = "apd", Label = "Tidak memakai helm", CreatedAt = now.AddHours(-2), IsCase = true, Status = "selesai", AssignedTo = "Siti Rahayu", HasSnapshot = true, Severity = "warning",
                    Notes = { new Note { Author = "Siti Rahayu", CreatedAt = now.AddHours(-1), Text = "Pekerja sudah ditegur dan memakai APD lengkap." } } },
                new Violation { Id = 4, CameraId = 5, CameraName = "CAM-05 Jalan Akses Tambang", Location = "Jalan Akses Tambang", Category = "vehicle", Label = "Penumpang di bak kendaraan", CreatedAt = now.AddHours(-5), IsCase = false, Status = "baru", AssignedTo = null, HasSnapshot = false, Severity = "warning" },
                new Violation { Id = 3, CameraId = 1, CameraName = "CAM-01 Gerbang Utama", Location = "Gerbang Utama", Category = "apd", Label = "Tidak memakai helm", CreatedAt = now.AddHours(-9), IsCase = false, Status = "baru", AssignedTo = null, HasSnapshot = true, Severity = "warning" },
                new Violation { Id = 2, CameraId = 4, CameraName = "CAM-04 Pos Satpam", Location = "Pos Satpam", Category = "vehicle", Label = "Penumpang di bak kendaraan", CreatedAt = now.AddDays(-1), IsCase = true, Status = "selesai", AssignedTo = "Budi Santoso", HasSnapshot = true, Severity = "critical" },
                new Violation { Id = 1, CameraId = 2, CameraName = "CAM-02 Area Produksi A", Location = "Area Produksi A", Category = "apd", Label = "Tidak memakai helm", CreatedAt = now.AddDays(-1).AddHours(-3), IsCase = false, Status = "baru", AssignedTo = null, HasSnapshot = false, Severity = "warning" },
            };
            return list.OrderByDescending(v => v.CreatedAt).ToList();
        }

        // Deterministic-looking pseudo-random daily count, seeded per
        // calendar date (not per range) so the same date always shows the
        // same numbers whether it's viewed via the 7-day dashboard window
        // or an arbitrary Reports date range — no real time-series DB.
        private static DailyCount DayValue(DateTime date)
        {
            var rnd = new Random(date.Year * 10000 + date.Month * 100 + date.Day);
            return new DailyCount { Date = date, Apd = rnd.Next(0, 8), Vehicle = rnd.Next(0, 6) };
        }

        public static List<DailyCount> DailyTrend(int days)
        {
            var start = DateTime.Today.AddDays(-(days - 1));
            return DailyTrend(start, DateTime.Today);
        }

        public static List<DailyCount> DailyTrend(DateTime start, DateTime end)
        {
            var list = new List<DailyCount>();
            for (var d = start.Date; d <= end.Date; d = d.AddDays(1))
            {
                list.Add(DayValue(d));
            }
            return list;
        }
    }
}
