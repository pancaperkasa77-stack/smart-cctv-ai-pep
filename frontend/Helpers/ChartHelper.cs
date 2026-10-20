using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Web;
using MvcSmartCctv.Models;

namespace MvcSmartCctv.Helpers
{
    // Server-side port of the SVG bar-chart builder duplicated in the
    // original static frontend's index.html (buildTrendSvg, 7-day fixed
    // window) and reports.html (same function, day-of-month labels +
    // adaptive stride for up to 31 days). Kept as one shared helper here
    // since this app has no client-side data fetching to drive a JS
    // version — the whole page renders server-side on each request.
    public static class ChartHelper
    {
        private static readonly string[] DayLabels = { "Min", "Sen", "Sel", "Rab", "Kam", "Jum", "Sab" };

        public static IHtmlString BuildTrendSvg(List<DailyCount> daily, bool dayOfMonthLabels)
        {
            const int W = 900;
            int H = dayOfMonthLabels ? 240 : 200;
            const int padL = 40, padR = 20, padT = 10;
            int padB = dayOfMonthLabels ? 50 : 34;
            int plotW = W - padL - padR, plotH = H - padT - padB;

            int maxVal = Math.Max(1, daily.SelectMany(d => new[] { d.Apd, d.Vehicle }).DefaultIfEmpty(0).Max());
            int niceMax = (int)Math.Ceiling(maxVal / 5.0) * 5;
            if (niceMax == 0) niceMax = 5;
            int n = Math.Max(1, daily.Count);
            double groupW = (double)plotW / n;
            double barW = Math.Min(22, groupW * 0.32);
            int stride = !dayOfMonthLabels ? 1 : (n <= 10 ? 1 : (n <= 16 ? 2 : (n <= 24 ? 3 : 5)));

            var sb = new StringBuilder();
            for (int i = 0; i <= 4; i++)
            {
                double y = padT + plotH - (plotH * i / 4.0);
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    "<line x1=\"{0}\" y1=\"{1}\" x2=\"{2}\" y2=\"{1}\" stroke=\"var(--border)\" stroke-width=\"1\"/>",
                    padL, y, W - padR);
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    "<text class=\"chart-label\" x=\"{0}\" y=\"{1}\" text-anchor=\"end\">{2}</text>",
                    padL - 8, y + 4, Math.Round(niceMax * i / 4.0));
            }

            for (int i = 0; i < daily.Count; i++)
            {
                var d = daily[i];
                double cx = padL + groupW * i + groupW / 2;
                double apdH = (double)d.Apd / niceMax * plotH;
                double vehH = (double)d.Vehicle / niceMax * plotH;
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    "<rect x=\"{0}\" y=\"{1}\" width=\"{2}\" height=\"{3}\" rx=\"3\" fill=\"#B57712\"/>",
                    cx - barW - 2, padT + plotH - apdH, barW, apdH);
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    "<rect x=\"{0}\" y=\"{1}\" width=\"{2}\" height=\"{3}\" rx=\"3\" fill=\"#B8021F\"/>",
                    cx + 2, padT + plotH - vehH, barW, vehH);

                if (!dayOfMonthLabels)
                {
                    string dayLabel = DayLabels[(int)d.Date.DayOfWeek];
                    sb.AppendFormat(CultureInfo.InvariantCulture,
                        "<text class=\"chart-label\" x=\"{0}\" y=\"{1}\" text-anchor=\"middle\">{2}</text>",
                        cx, H - padB + 18, dayLabel);
                }
                else if (i % stride == 0)
                {
                    sb.AppendFormat(CultureInfo.InvariantCulture,
                        "<text class=\"chart-label\" x=\"{0}\" y=\"{1}\" text-anchor=\"middle\">{2}</text>",
                        cx, H - padB + 18, d.Date.Day);
                }
            }

            return new HtmlString(sb.ToString());
        }
    }
}
