using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Web.Script.Serialization;
using MvcSmartCctv.Models;

namespace MvcSmartCctv.Services
{
    public class FastApiClient
    {
        private static readonly string BaseUrl = (Environment.GetEnvironmentVariable("FASTAPI_BASE_URL") ?? "http://127.0.0.1:8000").TrimEnd('/');
        private static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer();

        private static HttpClient CreateClient(string token = null)
        {
            var client = new HttpClient();
            client.BaseAddress = new Uri(BaseUrl + "/api/v1/");
            client.DefaultRequestHeaders.Accept.Clear();
            client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            if (!string.IsNullOrEmpty(token))
            {
                client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            }
            return client;
        }

        // ── Auth ──────────────────────────────────────────────────────────────────
        public static SessionUser Login(string username, string password)
        {
            using (var client = CreateClient())
            {
                var payload = Serializer.Serialize(new { username = username, password = password });
                var content = new StringContent(payload, Encoding.UTF8, "application/json");
                var resp = client.PostAsync("auth/login", content).Result;

                if (!resp.IsSuccessStatusCode) return null;

                var json = resp.Content.ReadAsStringAsync().Result;
                var data = Serializer.Deserialize<Dictionary<string, object>>(json);
                var token = data["access_token"].ToString();

                var userData = data["user"] as Dictionary<string, object>;
                var roleStr = userData["role"].ToString();
                var rolesList = new List<string> { roleStr };

                return new SessionUser
                {
                    Id = userData["id"].ToString(),
                    Username = userData["username"].ToString(),
                    DisplayName = userData["display_name"].ToString(),
                    Roles = rolesList,
                    IsAdmin = roleStr == "admin",
                    AccessToken = token,
                };
            }
        }

        // ── Dashboard ─────────────────────────────────────────────────────────────
        public static DashboardViewModel GetDashboard(string token)
        {
            using (var client = CreateClient(token))
            {
                var resp = client.GetAsync("violations/dashboard-stats").Result;
                if (!resp.IsSuccessStatusCode) return new DashboardViewModel();

                var json = resp.Content.ReadAsStringAsync().Result;
                var dict = Serializer.Deserialize<Dictionary<string, object>>(json);

                var vm = new DashboardViewModel
                {
                    TotalCameras = Convert.ToInt32(dict["total_cameras"]),
                    TotalViolations = Convert.ToInt32(dict["total_violations"]),
                    OpenCases = Convert.ToInt32(dict["open_cases"]),
                    Last24h = Convert.ToInt32(dict["last_24h"]),
                    ApdCameraCount = Convert.ToInt32(dict["apd_camera_count"]),
                    ApdViolationCount = Convert.ToInt32(dict["apd_violation_count"]),
                    VehicleCameraCount = Convert.ToInt32(dict["vehicle_camera_count"]),
                    VehicleViolationCount = Convert.ToInt32(dict["vehicle_violation_count"]),
                    Daily = GetReportsSummary(null, null, "7", token).Daily,
                    TopLocations = GetTopLocations(token),
                    Feed = ParseViolations(dict["feed"]),
                };
                return vm;
            }
        }

        private static List<TopLocationRow> GetTopLocations(string token)
        {
            var cameras = GetCameras(null, token);
            var violations = GetViolations(null, 100, token);
            return cameras.Select(c => new TopLocationRow
            {
                CameraName = c.Name,
                Location = c.Location,
                Category = c.Category,
                Count = violations.Count(v => v.CameraId == c.Id)
            }).OrderByDescending(r => r.Count).Take(5).ToList();
        }

        // ── Cameras ───────────────────────────────────────────────────────────────
        public static List<Camera> GetCameras(string category, string token)
        {
            using (var client = CreateClient(token))
            {
                var url = "cameras" + (string.IsNullOrEmpty(category) ? "" : "?category=" + category);
                var resp = client.GetAsync(url).Result;
                if (!resp.IsSuccessStatusCode) return new List<Camera>();

                var json = resp.Content.ReadAsStringAsync().Result;
                var list = Serializer.Deserialize<List<Dictionary<string, object>>>(json);

                var result = new List<Camera>();
                foreach (var d in list)
                {
                    result.Add(new Camera
                    {
                        Id = Convert.ToInt32(d["id"]),
                        Name = d["name"].ToString(),
                        IpAddress = d["ip_address"].ToString(),
                        Location = d["location"].ToString(),
                        Category = d["category"].ToString(),
                        StreamPath = d.ContainsKey("stream_path") && d["stream_path"] != null ? d["stream_path"].ToString() : null,
                    });
                }
                return result;
            }
        }

        // ── Violations ────────────────────────────────────────────────────────────
        public static CategoryStats GetCategoryStats(string category, string token)
        {
            try
            {
                using (var client = CreateClient(token))
                {
                    var resp = client.GetAsync("violations/category-stats?category=" + Uri.EscapeDataString(category)).Result;
                    if (!resp.IsSuccessStatusCode) return null;
                    var data = Serializer.Deserialize<Dictionary<string, object>>(resp.Content.ReadAsStringAsync().Result);
                    return new CategoryStats
                    {
                        TotalToday = Convert.ToInt32(data["total_today"]),
                        OpenCases = Convert.ToInt32(data["open_cases"]),
                    };
                }
            }
            catch (Exception) { return null; }
        }

        public static List<Violation> GetViolations(string category, int limit, string token)
        {
            using (var client = CreateClient(token))
            {
                var url = "violations?limit=" + Math.Max(1, Math.Min(100, limit)) + (string.IsNullOrEmpty(category) ? "" : "&category=" + category);
                var resp = client.GetAsync(url).Result;
                if (!resp.IsSuccessStatusCode) return new List<Violation>();

                var json = resp.Content.ReadAsStringAsync().Result;
                var list = Serializer.Deserialize<object>(json);
                return ParseViolations(list);
            }
        }

        private static List<Violation> ParseViolations(object listObj)
        {
            var result = new List<Violation>();
            if (listObj == null) return result;

            var list = listObj as System.Collections.ArrayList;
            if (list == null) return result;

            foreach (Dictionary<string, object> d in list)
            {
                var v = new Violation
                {
                    Id = Convert.ToInt32(d["id"]),
                    CameraId = d.ContainsKey("camera_id") && d["camera_id"] != null ? Convert.ToInt32(d["camera_id"]) : 0,
                    CameraName = d.ContainsKey("camera_name") && d["camera_name"] != null ? d["camera_name"].ToString() : "-",
                    Location = d.ContainsKey("location") && d["location"] != null ? d["location"].ToString() : "-",
                    Category = d["category"].ToString(),
                    Label = d["label"].ToString(),
                    Severity = d.ContainsKey("severity") && d["severity"] != null ? d["severity"].ToString() : "warning",
                    HasSnapshot = d.ContainsKey("has_snapshot") && Convert.ToBoolean(d["has_snapshot"]),
                    SnapshotPath = d.ContainsKey("snapshot_path") && d["snapshot_path"] != null ? d["snapshot_path"].ToString() : null,
                    IsCase = d.ContainsKey("is_case") && Convert.ToBoolean(d["is_case"]),
                    Status = d.ContainsKey("status") && d["status"] != null ? d["status"].ToString() : "baru",
                    AssignedTo = d.ContainsKey("assigned_to_name") && d["assigned_to_name"] != null ? d["assigned_to_name"].ToString() : null,
                    CreatedAt = d.ContainsKey("created_at") && d["created_at"] != null ? DateTime.Parse(d["created_at"].ToString()) : DateTime.Now,
                };

                if (d.ContainsKey("notes") && d["notes"] != null)
                {
                    var notesList = d["notes"] as System.Collections.ArrayList;
                    if (notesList != null)
                    {
                        foreach (Dictionary<string, object> nd in notesList)
                        {
                            v.Notes.Add(new Note
                            {
                                Author = nd.ContainsKey("author_name") && nd["author_name"] != null ? nd["author_name"].ToString() : "System",
                                CreatedAt = nd.ContainsKey("created_at") && nd["created_at"] != null ? DateTime.Parse(nd["created_at"].ToString()) : DateTime.Now,
                                Text = nd.ContainsKey("text") && nd["text"] != null ? nd["text"].ToString() : "",
                            });
                        }
                    }
                }
                result.Add(v);
            }
            return result;
        }

        // ── Cases ─────────────────────────────────────────────────────────────────
        public static List<Violation> GetCases(string filter, string token)
        {
            using (var client = CreateClient(token))
            {
                var url = "cases?limit=100&filter_type=" + Uri.EscapeDataString(string.IsNullOrEmpty(filter) ? "all" : filter);
                var resp = client.GetAsync(url).Result;
                if (!resp.IsSuccessStatusCode) return new List<Violation>();

                var json = resp.Content.ReadAsStringAsync().Result;
                var list = Serializer.Deserialize<object>(json);
                return ParseViolations(list);
            }
        }

        public static bool PromoteToCase(int violationId, string token)
        {
            using (var client = CreateClient(token))
            {
                var resp = client.PostAsync($"cases/{violationId}/promote", new StringContent("", Encoding.UTF8, "application/json")).Result;
                return resp.IsSuccessStatusCode;
            }
        }

        public static bool UpdateCaseStatus(int violationId, string status, string assignedToUserId, string token)
        {
            using (var client = CreateClient(token))
            {
                var dict = new Dictionary<string, object>();
                if (!string.IsNullOrEmpty(status)) dict["status"] = status;
                if (assignedToUserId != null) dict["assigned_to_user_id"] = assignedToUserId;

                var payload = Serializer.Serialize(dict);
                var request = new HttpRequestMessage(new HttpMethod("PATCH"), $"cases/{violationId}")
                {
                    Content = new StringContent(payload, Encoding.UTF8, "application/json")
                };

                var resp = client.SendAsync(request).Result;
                return resp.IsSuccessStatusCode;
            }
        }

        public static bool AddCaseNote(int violationId, string text, string token)
        {
            using (var client = CreateClient(token))
            {
                var payload = Serializer.Serialize(new { text = text });
                var content = new StringContent(payload, Encoding.UTF8, "application/json");
                var resp = client.PostAsync($"cases/{violationId}/notes", content).Result;
                return resp.IsSuccessStatusCode;
            }
        }

        // ── Reports ───────────────────────────────────────────────────────────────
        public static ReportsViewModel GetReportsSummary(string fromDate, string toDate, string quick, string token)
        {
            using (var client = CreateClient(token))
            {
                var queryParams = new List<string>();
                if (!string.IsNullOrEmpty(fromDate)) queryParams.Add("from=" + fromDate);
                if (!string.IsNullOrEmpty(toDate)) queryParams.Add("to=" + toDate);
                if (!string.IsNullOrEmpty(quick)) queryParams.Add("quick=" + quick);

                var url = "reports" + (queryParams.Count > 0 ? "?" + string.Join("&", queryParams) : "");
                var resp = client.GetAsync(url).Result;
                if (!resp.IsSuccessStatusCode)
                {
                    return new ReportsViewModel { RangeError = "Gagal mengambil data dari server." };
                }

                var json = resp.Content.ReadAsStringAsync().Result;
                var d = Serializer.Deserialize<Dictionary<string, object>>(json);

                var vm = new ReportsViewModel
                {
                    Start = DateTime.Parse(d["start_date"].ToString()),
                    End = DateTime.Parse(d["end_date"].ToString()),
                    QuickActive = quick,
                    RangeLabel = d["range_label"].ToString(),
                    Total = Convert.ToInt32(d["total_violations"]),
                    AvgPerDay = Convert.ToDouble(d["avg_per_day"]),
                    CaseTotal = Convert.ToInt32(d["case_total"]),
                    CompletionRate = d["completion_rate"] != null ? (double?)Convert.ToDouble(d["completion_rate"]) : null,
                    AvgResponseMinutes = d["avg_response_minutes"] != null ? (double?)Convert.ToDouble(d["avg_response_minutes"]) : null,
                    ApdTotal = Convert.ToInt32(d["apd_total"]),
                    VehicleTotal = Convert.ToInt32(d["vehicle_total"]),
                    ApdPct = Convert.ToInt32(d["apd_pct"]),
                    VehiclePct = Convert.ToInt32(d["vehicle_pct"]),
                };

                if (d.ContainsKey("daily_trend") && d["daily_trend"] != null)
                {
                    var dtList = d["daily_trend"] as System.Collections.ArrayList;
                    if (dtList != null)
                    {
                        foreach (Dictionary<string, object> dtD in dtList)
                        {
                            vm.Daily.Add(new DailyCount
                            {
                                Date = DateTime.Parse(dtD["date"].ToString()),
                                Apd = Convert.ToInt32(dtD["apd"]),
                                Vehicle = Convert.ToInt32(dtD["vehicle"]),
                            });
                        }
                    }
                }

                if (d.ContainsKey("by_camera") && d["by_camera"] != null)
                {
                    var bcList = d["by_camera"] as System.Collections.ArrayList;
                    if (bcList != null)
                    {
                        foreach (Dictionary<string, object> bcD in bcList)
                        {
                            vm.ByCamera.Add(new CameraReportRow
                            {
                                CameraName = bcD["camera_name"].ToString(),
                                Location = bcD["location"].ToString(),
                                Category = bcD["category"].ToString(),
                                Count = Convert.ToInt32(bcD["count"]),
                            });
                        }
                    }
                }

                return vm;
            }
        }

        // ── Users (Admin) ─────────────────────────────────────────────────────────
        public static List<PlatformUser> GetUsers(string token)
        {
            using (var client = CreateClient(token))
            {
                var resp = client.GetAsync("auth/users").Result;
                if (!resp.IsSuccessStatusCode) return new List<PlatformUser>();

                var json = resp.Content.ReadAsStringAsync().Result;
                var list = Serializer.Deserialize<List<Dictionary<string, object>>>(json);

                var result = new List<PlatformUser>();
                foreach (var d in list)
                {
                    var rStr = d["role"].ToString();
                    result.Add(new PlatformUser
                    {
                        Id = d["id"].ToString(),
                        Username = d["username"].ToString(),
                        FirstName = d.ContainsKey("first_name") && d["first_name"] != null ? d["first_name"].ToString() : "",
                        LastName = d.ContainsKey("last_name") && d["last_name"] != null ? d["last_name"].ToString() : "",
                        Email = d.ContainsKey("email") && d["email"] != null ? d["email"].ToString() : null,
                        Roles = new List<string> { rStr },
                        Enabled = Convert.ToBoolean(d["is_enabled"]),
                    });
                }
                return result;
            }
        }

        public static bool CreateUser(string username, string firstName, string lastName, string email, string role, string password, string token)
        {
            using (var client = CreateClient(token))
            {
                var payload = Serializer.Serialize(new
                {
                    username = username,
                    first_name = firstName,
                    last_name = lastName,
                    email = email,
                    role = string.IsNullOrEmpty(role) ? "operator" : role,
                    password = password,
                    is_enabled = true
                });
                var content = new StringContent(payload, Encoding.UTF8, "application/json");
                var resp = client.PostAsync("auth/users", content).Result;
                return resp.IsSuccessStatusCode;
            }
        }

        public static bool ToggleUser(string id, string token)
        {
            using (var client = CreateClient(token))
            {
                var resp = client.PostAsync($"auth/users/{id}/toggle", new StringContent("", Encoding.UTF8, "application/json")).Result;
                return resp.IsSuccessStatusCode;
            }
        }
    }
}
