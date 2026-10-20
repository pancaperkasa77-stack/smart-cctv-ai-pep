using System.Web.Mvc;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class ReportsController : BaseController
    {
        public ActionResult Index(string from, string to, string quick)
        {
            SetShell("reports");
            ViewBag.Title = "Laporan & Analitik";

            var vm = FastApiClient.GetReportsSummary(from, to, quick, CurrentUser.AccessToken);
            if (vm.ByCamera.Count > 0)
            {
                var maxCount = 1;
                foreach (var c in vm.ByCamera) { if (c.Count > maxCount) maxCount = c.Count; }
                ViewBag.MaxCameraCount = maxCount;
            }
            else
            {
                ViewBag.MaxCameraCount = 1;
            }

            return View(vm);
        }
    }
}
