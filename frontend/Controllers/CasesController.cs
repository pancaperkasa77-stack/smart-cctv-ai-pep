using System;
using System.Web.Mvc;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class CasesController : BaseController
    {
        public ActionResult Index(string filter)
        {
            SetShell("cases");
            ViewBag.Title = "Case Violation";
            if (filter != "apd" && filter != "vehicle" && filter != "open") filter = "all";
            return View(new CasesViewModel
            {
                ActiveFilter = filter,
                Rows = FastApiClient.GetCases(filter, CurrentUser.AccessToken),
            });
        }

        private bool TrySave(Func<bool> save)
        {
            try { return save(); }
            catch (Exception) { return false; }
        }

        private ActionResult Finish(int id, bool saved, string redirectUrl)
        {
            TempData["CaseMessage"] = saved ? "Perubahan tersimpan." : "Perubahan belum terkonfirmasi. Periksa data sebelum mencoba kembali.";
            TempData["CaseFailed"] = !saved;
            if (!saved) TempData["CaseReopenId"] = id;
            if (!string.IsNullOrEmpty(redirectUrl) && Url.IsLocalUrl(redirectUrl)) return Redirect(redirectUrl);
            return RedirectToAction("Index");
        }

        [HttpPost, ValidateAntiForgeryToken]
        public ActionResult Promote(int id, string redirectUrl)
        {
            return Finish(id, id > 0 && TrySave(() => FastApiClient.PromoteToCase(id, CurrentUser.AccessToken)), redirectUrl);
        }

        [HttpPost, ValidateAntiForgeryToken]
        public ActionResult UpdateStatus(int id, string status, string redirectUrl)
        {
            var valid = status == "baru" || status == "diproses" || status == "selesai";
            return Finish(id, id > 0 && valid && TrySave(() => FastApiClient.UpdateCaseStatus(id, status, null, CurrentUser.AccessToken)), redirectUrl);
        }

        [HttpPost, ValidateAntiForgeryToken]
        public ActionResult AddNote(int id, string text, string redirectUrl)
        {
            var saved = id > 0 && !string.IsNullOrWhiteSpace(text) && TrySave(() => FastApiClient.AddCaseNote(id, text, CurrentUser.AccessToken));
            if (!saved) TempData["CaseNoteDraft"] = text ?? "";
            return Finish(id, saved, redirectUrl);
        }
    }
}
