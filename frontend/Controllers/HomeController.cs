using System.Web.Mvc;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class HomeController : BaseController
    {
        public ActionResult Index()
        {
            SetShell("dashboard");
            ViewBag.Title = "Dashboard";

            var vm = FastApiClient.GetDashboard(CurrentUser.AccessToken);
            return View(vm);
        }
    }
}
