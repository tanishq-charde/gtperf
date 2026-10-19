Attribute VB_Name = "gtperf"
' gtperf worksheet functions and macros for the site performance workbook.
'
' Import into the workbook made by `gtperf excel` (Alt+F11, File > Import File)
' and save it as .xlsm. The worksheet functions read the named ranges on the
' Tables sheet, so they work in any cell of that workbook:
'
'   =GT_SITE("power", 30, 250, 70, 8, 15)      site output, MW
'   =GT_SITE_HEAT_RATE(30, 250, 70, 8, 15)     site heat rate, kJ/kWh
'   =GT_ISA_PRESSURE(250)                      ISA pressure, mbar
'   =GT_INTERP(x, xs, ys)                      linear interpolation
'
' RunFullModel sends every case on the Site tool sheet to the Python model
' (`gtperf site --csv`) and writes the full-model results under the sheet, so
' the correction-curve answer can be checked or a fuel change run. It needs
' Windows and gtperf on the PATH; the command and datasheet path are the named
' cells GTPERF_COMMAND and DATASHEET_PATH on the About sheet.
Option Explicit

Private Const P_ISO As Double = 1013.25   ' mbar
Private Const RH_REF As Double = 0.6

' --- worksheet functions ----------------------------------------------------

Public Function GT_INTERP(ByVal x As Double, xs As Range, ys As Range) As Double
    ' Linear interpolation in ascending xs; extrapolates from the end segments.
    Dim n As Long, i As Long
    n = xs.Rows.Count
    i = 1
    Do While i < n - 1
        If xs.Cells(i + 1, 1).Value > x Then Exit Do
        i = i + 1
    Loop
    Dim x0 As Double, x1 As Double, y0 As Double, y1 As Double
    x0 = xs.Cells(i, 1).Value: x1 = xs.Cells(i + 1, 1).Value
    y0 = ys.Cells(i, 1).Value: y1 = ys.Cells(i + 1, 1).Value
    GT_INTERP = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
End Function

Public Function GT_ISA_PRESSURE(ByVal altitude_m As Double) As Double
    ' ISA static pressure in mbar (troposphere).
    GT_ISA_PRESSURE = P_ISO * (1 - 0.0000225577 * altitude_m) ^ 5.25588
End Function

Public Function GT_SAT_PRESSURE(ByVal temperature_C As Double) As Double
    ' Saturation vapour pressure of water, mbar (Buck 1996, over water).
    Dim t As Double
    t = temperature_C
    GT_SAT_PRESSURE = 6.1121 * Exp((18.678 - t / 234.5) * (t / (257.14 + t)))
End Function

Public Function GT_HUMIDITY_RATIO(ByVal temperature_C As Double, ByVal rh_pct As Double, _
                                  ByVal pressure_mbar As Double) As Double
    ' Specific humidity ratio, kg water per kg dry air.
    Dim pv As Double
    pv = rh_pct / 100 * GT_SAT_PRESSURE(temperature_C)
    GT_HUMIDITY_RATIO = 0.621945 * pv / (pressure_mbar - pv)
End Function

Public Function GT_TABLE(ByVal column_name As String, ByVal temperature_C As Double) As Double
    ' A Tables column (e.g. "t_power") interpolated at an ambient temperature.
    Application.Volatile
    GT_TABLE = GT_INTERP(temperature_C, NamedRange("T_GRID"), NamedRange(UCase(column_name)))
End Function

Public Function GT_SITE(ByVal quantity As String, ByVal temperature_C As Double, ByVal altitude_m As Double, _
                        ByVal rh_pct As Double, ByVal inlet_loss_mbar As Double, _
                        ByVal exhaust_loss_mbar As Double, Optional ByVal degradation_power_pct As Double = 0, _
                        Optional ByVal degradation_heat_rate_pct As Double = 0) As Variant
    ' Site performance by the same correction method as the Site tool sheet.
    ' quantity: power (MW), heat_rate (kJ/kWh), efficiency (%), exhaust_flow (kg/s), exhaust_temp (degC).
    Application.Volatile
    Dim delta As Double, drh As Double, din As Double, dex As Double
    delta = GT_ISA_PRESSURE(altitude_m) / P_ISO
    drh = rh_pct / 100 - RH_REF
    din = inlet_loss_mbar   ' losses are at rated flow and scale with dynamic head: no altitude correction
    dex = exhaust_loss_mbar
    Dim q As String
    q = LCase(Trim(quantity))
    Select Case q
        Case "power"
            GT_SITE = NamedValue("ISO_POWER_MW") * Factor("power", temperature_C, delta, drh, din, dex) _
                      * (1 - degradation_power_pct / 100)
        Case "heat_rate"
            GT_SITE = NamedValue("ISO_HEAT_RATE") * Factor("heat_rate", temperature_C, 1, drh, din, dex) _
                      * (1 + degradation_heat_rate_pct / 100)
        Case "efficiency"
            GT_SITE = 360000 / GT_SITE("heat_rate", temperature_C, altitude_m, rh_pct, inlet_loss_mbar, _
                                       exhaust_loss_mbar, degradation_power_pct, degradation_heat_rate_pct)
        Case "exhaust_flow"
            GT_SITE = NamedValue("ISO_EXHAUST_FLOW") * Factor("exhaust_flow", temperature_C, delta, drh, din, dex)
        Case "exhaust_temp"
            GT_SITE = NamedValue("ISO_EXHAUST_TEMP_C") + GT_TABLE("t_exhaust_temp", temperature_C) _
                      + GT_TABLE("rh_exhaust_temp", temperature_C) * drh _
                      + GT_TABLE("inlet_exhaust_temp", temperature_C) * din _
                      + GT_TABLE("exhaust_exhaust_temp", temperature_C) * dex
        Case Else
            GT_SITE = CVErr(xlErrValue)
    End Select
End Function

Public Function GT_SITE_POWER(ByVal temperature_C As Double, ByVal altitude_m As Double, ByVal rh_pct As Double, _
                              ByVal inlet_loss_mbar As Double, ByVal exhaust_loss_mbar As Double) As Variant
    GT_SITE_POWER = GT_SITE("power", temperature_C, altitude_m, rh_pct, inlet_loss_mbar, exhaust_loss_mbar)
End Function

Public Function GT_SITE_HEAT_RATE(ByVal temperature_C As Double, ByVal altitude_m As Double, _
                                  ByVal rh_pct As Double, ByVal inlet_loss_mbar As Double, _
                                  ByVal exhaust_loss_mbar As Double) As Variant
    GT_SITE_HEAT_RATE = GT_SITE("heat_rate", temperature_C, altitude_m, rh_pct, inlet_loss_mbar, exhaust_loss_mbar)
End Function

' --- full model ---------------------------------------------------------------

Public Sub RunFullModel()
    ' Run every case on the Site tool sheet through `gtperf site --csv` and write the results below it.
    Dim ws As Worksheet, j As Long, col As Long, r As Long
    Set ws = ThisWorkbook.Worksheets("Site tool")
    Dim rowName As Long, rowT As Long, rowAlt As Long, rowRH As Long, rowIn As Long, rowEx As Long
    Dim rowDegP As Long, rowDegHR As Long, rowPower As Long, rowHR As Long, rowW As Long, rowTex As Long
    rowName = FindRow(ws, "Case name"): rowT = FindRow(ws, "Ambient temperature")
    rowAlt = FindRow(ws, "Site altitude"): rowRH = FindRow(ws, "Relative humidity")
    rowIn = FindRow(ws, "Inlet pressure loss"): rowEx = FindRow(ws, "Exhaust pressure loss")
    rowDegP = FindRow(ws, "Degradation, output"): rowDegHR = FindRow(ws, "Degradation, heat rate")
    rowPower = FindRow(ws, "Electrical output"): rowHR = FindRow(ws, "Heat rate (LHV)")
    rowW = FindRow(ws, "Exhaust flow"): rowTex = FindRow(ws, "Exhaust temperature")

    Dim outRow As Long
    outRow = ws.UsedRange.Row + ws.UsedRange.Rows.Count + 1
    ws.Cells(outRow, 2).Value = "Full model (gtperf site), run " & Format(Now, "yyyy-mm-dd hh:nn")
    ws.Cells(outRow, 2).Font.Bold = True
    Dim labels As Variant, units As Variant, srcRows As Variant, params As Variant
    labels = Array("Electrical output", "Heat rate (LHV)", "Exhaust flow", "Exhaust temperature", _
                   "Output, workbook minus model")
    units = Array("MW", "kJ/kWh", "kg/s", "°C", "%")
    params = Array("Electrical output", "Heat rate (LHV)", "Exhaust flow", "Exhaust temperature")
    For r = 0 To 4
        ws.Cells(outRow + 1 + r, 2).Value = labels(r)
        ws.Cells(outRow + 1 + r, 3).Value = units(r)
    Next r

    Dim tmp As String, toml As String, csv As String, v As Double
    col = 4
    Do While Len(ws.Cells(rowName, col).Value) > 0
        toml = BuildSiteToml(ws.Cells(rowName, col).Value, ws.Cells(rowT, col).Value, ws.Cells(rowAlt, col).Value, _
                             ws.Cells(rowRH, col).Value, ws.Cells(rowIn, col).Value, ws.Cells(rowEx, col).Value)
        tmp = Environ$("TEMP") & "\gtperf_case_" & col & ".toml"
        WriteText tmp, toml
        csv = RunCommand(NamedValue("GTPERF_COMMAND") & " site """ & NamedValue("DATASHEET_PATH") & """ """ _
                         & tmp & """ --csv")
        For j = 0 To 3
            v = ParseSiteCsv(csv, CStr(params(j)))
            If j = 0 Then v = v * (1 - ws.Cells(rowDegP, col).Value / 100)
            If j = 1 Then v = v * (1 + ws.Cells(rowDegHR, col).Value / 100)
            ws.Cells(outRow + 1 + j, col).Value = v
            ws.Cells(outRow + 1 + j, col).NumberFormat = ws.Cells(Choose(j + 1, rowPower, rowHR, rowW, rowTex), _
                                                                  col).NumberFormat
        Next j
        ws.Cells(outRow + 5, col).Value = 100 * (ws.Cells(rowPower, col).Value / ws.Cells(outRow + 1, col).Value - 1)
        ws.Cells(outRow + 5, col).NumberFormat = "0.00"
        col = col + 1
    Loop
End Sub

Public Function BuildSiteToml(ByVal name As String, ByVal temperature_C As Double, ByVal altitude_m As Double, _
                              ByVal rh_pct As Double, ByVal inlet_loss_mbar As Double, _
                              ByVal exhaust_loss_mbar As Double) As String
    ' Site file in the format `gtperf site` reads. Str() writes a decimal point in any locale.
    Dim s As String
    s = "name = """ & Replace(name, """", "'") & """" & vbLf
    s = s & "temperature_C = " & TomlNumber(temperature_C) & vbLf
    s = s & "altitude_m = " & TomlNumber(altitude_m) & vbLf
    s = s & "rh = " & TomlNumber(rh_pct / 100) & vbLf
    s = s & "inlet_loss_mbar = " & TomlNumber(inlet_loss_mbar) & vbLf
    s = s & "exhaust_loss_mbar = " & TomlNumber(exhaust_loss_mbar) & vbLf
    BuildSiteToml = s
End Function

Public Function ParseSiteCsv(ByVal csv As String, ByVal parameter As String) As Double
    ' The site column for one parameter of `gtperf site --csv` output. Val() reads a decimal point in any locale.
    Dim lines() As String, f() As String, i As Long
    lines = Split(Replace(csv, vbCr, ""), vbLf)
    For i = 0 To UBound(lines)
        f = Split(lines(i), ",")
        If UBound(f) >= 3 Then
            If f(0) = parameter Then
                ParseSiteCsv = Val(f(3))
                Exit Function
            End If
        End If
    Next i
    Err.Raise vbObjectError + 513, "ParseSiteCsv", "Parameter not found in gtperf output: " & parameter
End Function

' --- helpers ------------------------------------------------------------------

Private Function TomlNumber(ByVal x As Double) As String
    ' Str() writes ".5" and "-.5"; TOML needs the leading zero.
    Dim s As String
    s = Trim(Str(x))
    If Left(s, 1) = "." Then s = "0" & s
    If Left(s, 2) = "-." Then s = "-0" & Mid(s, 2)
    TomlNumber = s
End Function

Private Function Factor(ByVal q As String, ByVal t As Double, ByVal alt As Double, ByVal drh As Double, _
                        ByVal din As Double, ByVal dex As Double) As Double
    Factor = GT_TABLE("t_" & q, t) * alt * (1 + GT_TABLE("rh_" & q, t) * drh) _
             * (1 + GT_TABLE("inlet_" & q, t) * din) * (1 + GT_TABLE("exhaust_" & q, t) * dex)
End Function

Private Function NamedRange(ByVal nm As String) As Range
    Set NamedRange = ThisWorkbook.Names(nm).RefersToRange
End Function

Private Function NamedValue(ByVal nm As String) As Variant
    NamedValue = NamedRange(nm).Cells(1, 1).Value
End Function

Private Function FindRow(ws As Worksheet, ByVal label As String, Optional ByVal occurrence As Long = 1) As Long
    Dim r As Long, seen As Long
    For r = 1 To ws.UsedRange.Row + ws.UsedRange.Rows.Count
        If ws.Cells(r, 2).Value = label Then
            seen = seen + 1
            If seen = occurrence Then
                FindRow = r
                Exit Function
            End If
        End If
    Next r
    Err.Raise vbObjectError + 514, "FindRow", "Row not found on the Site tool sheet: " & label
End Function

Private Sub WriteText(ByVal path As String, ByVal text As String)
    Dim f As Integer
    f = FreeFile
    Open path For Output As #f
    Print #f, text;
    Close #f
End Sub

Private Function RunCommand(ByVal cmd As String) As String
    Dim ex As Object
    Set ex = CreateObject("WScript.Shell").Exec("cmd /c " & cmd)
    Do While ex.Status = 0
        DoEvents
    Loop
    If ex.ExitCode <> 0 Then
        Err.Raise vbObjectError + 515, "RunCommand", "gtperf failed: " & ex.StdErr.ReadAll
    End If
    RunCommand = ex.StdOut.ReadAll
End Function
