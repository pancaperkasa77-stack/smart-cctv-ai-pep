using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    public class CasesViewModel
    {
        public string ActiveFilter; // "all" | "apd" | "vehicle" | "open"
        public List<Violation> Rows;
    }
}
