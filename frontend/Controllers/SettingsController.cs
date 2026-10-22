using System;
using System.Globalization;
using System.Web.Mvc;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class SettingsController : BaseController
    {
        [HttpGet]
        public ActionResult Index()
        {
            var model = new RetentionViewModel();
            if (CurrentUser.IsAdmin)
            {
                LoadSummary(model);
                if (model.Summary != null)
                {
                    model.Enabled = model.Summary.enabled;
                    model.MaxRecords = model.Summary.max_records.ToString(CultureInfo.InvariantCulture);
                }
                model.Notice = TempData["RetentionNotice"] as string;
            }
            return SettingsView(model);
        }

        [HttpPost]
        [ValidateAntiForgeryToken]
        public ActionResult PreviewRetention(bool enabled = false, string maxRecords = null)
        {
            if (!CurrentUser.IsAdmin) return new HttpStatusCodeResult(403);
            var model = new RetentionViewModel { Enabled = enabled, MaxRecords = maxRecords };
            int count;
            if (ValidateCount(model, out count))
            {
                try
                {
                    model.Preview = RetentionApiClient.Preview(enabled, count, CurrentUser.AccessToken);
                    if (string.IsNullOrEmpty(model.Preview.preview_token) || model.Preview.enabled != enabled || model.Preview.max_records != count)
                    {
                        model.Preview = null;
                        model.Error = "Respons pratinjau tidak valid. Buat pratinjau baru.";
                    }
                }
                catch (RetentionApiException ex) { model.Error = ex.Message; }
            }
            LoadSummary(model);
            return SettingsView(model);
        }

        [HttpPost]
        [ValidateAntiForgeryToken]
        public ActionResult SaveRetention(bool enabled = false, string maxRecords = null, string previewToken = null, string version = null, bool confirmed = false)
        {
            if (!CurrentUser.IsAdmin) return new HttpStatusCodeResult(403);
            // Read the exact preview payload from this form, never from current settings.
            // The API verifies its signed token and configuration version before saving.
            var model = new RetentionViewModel { Enabled = enabled, MaxRecords = maxRecords };
            int count;
            long parsedVersion;
            if (ValidateCount(model, out count))
            {
                if (!confirmed || string.IsNullOrWhiteSpace(previewToken) || !long.TryParse(version, NumberStyles.None, CultureInfo.InvariantCulture, out parsedVersion))
                    model.Error = "Konfirmasi diperlukan. Buat pratinjau baru sebelum menyimpan.";
                else
                {
                    try
                    {
                        RetentionApiClient.Save(enabled, count, previewToken, parsedVersion, CurrentUser.AccessToken);
                        TempData["RetentionNotice"] = enabled
                            ? "Pengaturan disimpan. Pembersihan dijadwalkan di latar belakang secara bertahap; jumlah data tidak langsung berkurang."
                            : "Pengaturan disimpan. Retensi nonaktif. Penghapusan berkas yang sudah dijadwalkan dapat tetap diselesaikan.";
                        return RedirectToAction("Index");
                    }
                    catch (RetentionApiException ex) { model.Error = ex.Message; }
                }
            }
            // Failed saves require a fresh preview; never silently retry a destructive request.
            LoadSummary(model);
            return SettingsView(model);
        }

        [HttpPost]
        [ValidateAntiForgeryToken]
        public ActionResult SavePlayback(string mode = null, string version = null)
        {
            if (!CurrentUser.IsAdmin) return new HttpStatusCodeResult(403);
            long parsedVersion;
            if (!ModelState.IsValid || (mode != "raw" && mode != "ai") ||
                !long.TryParse(version, NumberStyles.None, CultureInfo.InvariantCulture, out parsedVersion) || parsedVersion < 0)
                ViewBag.PlaybackError = "Pilih RAW atau DeteksiAI dengan versi pengaturan yang valid. Muat ulang status sebelum mencoba lagi.";
            else
            {
                try
                {
                    PlaybackApiClient.Save(mode, parsedVersion, CurrentUser.AccessToken);
                    TempData["PlaybackNotice"] = "Mode pemutaran global disimpan. Deteksi dan penyimpanan data di backend tetap berjalan.";
                    return RedirectToAction("Index");
                }
                catch (PlaybackApiException ex) { ViewBag.PlaybackError = ex.Message; }
            }
            var model = new RetentionViewModel();
            LoadSummary(model);
            if (model.Summary != null)
            {
                model.Enabled = model.Summary.enabled;
                model.MaxRecords = model.Summary.max_records.ToString(CultureInfo.InvariantCulture);
            }
            return SettingsView(model);
        }

        private bool ValidateCount(RetentionViewModel model, out int count)
        {
            if (!ModelState.IsValid || !int.TryParse(model.MaxRecords, NumberStyles.None, CultureInfo.InvariantCulture, out count) || count < 1 || count > 1000000)
            {
                count = 0;
                model.Error = "Jumlah data harus berupa bilangan bulat antara 1 dan 1000000.";
                return false;
            }
            return true;
        }

        private void LoadSummary(RetentionViewModel model)
        {
            try { model.Summary = RetentionApiClient.Get(CurrentUser.AccessToken); }
            catch (RetentionApiException ex)
            {
                model.Error = string.IsNullOrEmpty(model.Error) ? ex.Message : model.Error + " Status terkini tidak dapat dimuat.";
                model.Preview = null;
            }
        }

        private ActionResult SettingsView(RetentionViewModel model)
        {
            SetShell("settings");
            ViewBag.Title = "Pengaturan";
            ViewBag.IsAdmin = CurrentUser.IsAdmin;
            if (CurrentUser.IsAdmin)
            {
                ViewBag.PlaybackNotice = TempData["PlaybackNotice"] as string;
                try { model.Playback = PlaybackApiClient.Get(CurrentUser.AccessToken); }
                catch (PlaybackApiException ex)
                {
                    var previousError = ViewBag.PlaybackError as string;
                    ViewBag.PlaybackError = string.IsNullOrEmpty(previousError) ? ex.Message : previousError + " " + ex.Message;
                }
                try { model.Cameras = FastApiClient.GetCameras(null, CurrentUser.AccessToken); }
                catch (Exception) { model.CameraError = "Daftar kamera tidak dapat dimuat."; }
            }
            return View("Index", model);
        }
    }
}
