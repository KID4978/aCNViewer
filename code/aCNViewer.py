import math
import types
import copy
import os
import glob
import argparse
import textwrap

from MultiDict import *
from FileHandler import *
from commandLineUtils import *
from position import Position
from Cluster import ThreadManager, _getNbAvailableCpus, guessHpc
from Utilities import *
from annovar import ProcessFileFromCluster, Cmd
from R import R
from color import Color
from coverage import Coverage, OrientedPosition
from FileHandler import ParseFastaFile, MergeAnnotationFiles

_isCustom = False


class RunTQN:

    def __init__(self, binDir=None):
        self.__binDir = binDir

    def __getHeaderForTQNinput(self, header):
        currentHeader = ['Name', 'Chr', 'Position']
        return currentHeader +\
            [columnName for columnName in header if columnName not in
             currentHeader + ['SNP Name']]

    def __getOutFhForSampleName(self, sampleName, fhDict, targetDir, fileName,
                                header):
        outFh = fhDict.get(sampleName)
        if not outFh:
            outFileName = FileNameGetter(os.path.basename(fileName))
            outFileName = outFileName.get('_%s.txt' % sampleName)
            outFileName = os.path.join(targetDir, outFileName)
            outFh = CsvFileWriter(outFileName)
            outFh.write(self.__getHeaderForTQNinput(header))
            fhDict[sampleName] = outFh
        return outFh

    def __getTQNlineToWrite(self, splittedLine, header, idxToExcludeList):
        return [value for i, value in enumerate(splittedLine) if i not in
                idxToExcludeList]

    def _splitFinalReportBySample(self, fileName, targetDir, snpFile,
                                  sampleList=None):
        snpPosDict = RunAscat()._getSnpDictFromFile(snpFile)
        Utilities.mySystem('mkdir -p %s' % targetDir)
        fh, sampleName, header, outFileName = \
            self._getFhSampleNameHeaderAndOutFileNameFromFile(
                fileName, targetDir)
        fhDict = {}
        sampleIdIdx = header.index('Sample ID')
        snpIdx = header.index('SNP Name')
        idxToExcludeList = [snpIdx]
        if 'Chr' in header:
            idxToExcludeList.append(header.index('Chr'))
        if 'Position' in header:
            idxToExcludeList.append(header.index('Position'))
        for splittedLine in fh:
            sampleName = splittedLine[sampleIdIdx]
            if sampleList and sampleName not in sampleList:
                print 'Passing sample %s' % sampleName
                continue
            snpName = splittedLine[snpIdx]
            if snpName not in snpPosDict:
                continue
            chrName, pos = snpPosDict[snpName]
            outFh = self.__getOutFhForSampleName(sampleName, fhDict, targetDir,
                                                 fileName, header)
            outFh.write([snpName, chrName, pos] + self.__getTQNlineToWrite(
                splittedLine, header, idxToExcludeList))

    def __getBeadchipFromIlluminaFinalReportFile(self, fileName):
        fh = ReadFileAtOnceParser(fileName)
        for splittedLine in fh:
            if splittedLine[0] == 'Content':
                return splittedLine[2]

    def _getBeadchipList(self):
        beadchipList = []
        for clusterFile in glob.glob(os.path.join(self.__binDir, 'tQN', 'lib',
                                                  '*clusters.txt')):
            beadchip = os.path.basename(clusterFile).split('_tQN')[0]
            beadchipList.append(beadchip)
        return beadchipList

    def __getMatchLengthForBeadchips(self, name1, name2):
        name1 = name1.lower()
        name2 = name2.lower()
        minLength = min(len(name1), len(name2))
        nbMatches = 0
        for i in range(minLength):
            if name1[i] == name2[i]:
                nbMatches += 1
        return nbMatches

    def _getBeadchipNameFromIlluminaReportFile(self, fileName):
        fileName = self.__getBeadchipFromIlluminaFinalReportFile(fileName)

        beadchipList = self._getBeadchipList()
        resDict = defaultdict(list)
        for beadchip in beadchipList:
            nbMatches = self.__getMatchLengthForBeadchips(beadchip, fileName)
            resDict[nbMatches].append(beadchip)
        print beadchipList, resDict
        beadchipList = resDict[max(resDict.keys())]
        if len(beadchipList) != 1:
            raise NotImplementedError('Could not automatically choose beadchip \
            from bpm file name %s: matching beadchips are:\n%s' %
                                      (fileName, '\n'.join(beadchipList)))
        return beadchipList[0]

    def _passHeader(self, fh):
        found = False
        for splittedLine in fh:
            if splittedLine[0] == '[Data]':
                found = True
                break
        if not found:
            raise NotImplementedError('Could not find "[Data]" in file...')

    def _getFhSampleNameHeaderAndOutFileNameFromFile(self, fileName,
                                                     targetDir):
        fh = ReadFileAtOnceParser(fileName)
        self._passHeader(fh)
        header = fh.getSplittedLine()
        sampleIdIdx = header.index('Sample ID')
        splittedLine = fh.getSplittedLine()
        sampleName = splittedLine[sampleIdIdx]
        fh.restore(splittedLine)
        outFileName = os.path.join(targetDir, splittedLine[sampleIdIdx] +
                                   '_extracted.txt')
        return fh, sampleName, header, outFileName

    def _formatFinalReportFile(self, fileName, targetDir):
        fh, sampleName, header, outFileName =\
            self._getFhSampleNameHeaderAndOutFileNameFromFile(fileName,
                                                              targetDir)
        # logRidx = header.index('Log R Ratio')
        # bafIdx = header.index('B Allele Freq')
        xIdx = header.index('X')
        yIdx = header.index('Y')
        sampleIdIdx = header.index('Sample ID')
        if header[:3] != ['SNP Name', 'Chr', 'Position']:
            raise NotImplementedError('Expecting first 3 columns in header ')
        outFh = CsvFileWriter(outFileName)
        outFh.write(['Name'] + header[1:3] + [header[xIdx], header[yIdx]])
        for splittedLine in fh:
            outFh.write(splittedLine[:3] + [splittedLine[xIdx],
                                            splittedLine[yIdx]])
        return outFileName

    def __createSampleFile(self, sampleName, sampleDir):
        sampleFile = os.path.join(sampleDir, 'sample_names.txt')
        outFh = CsvFileWriter(sampleFile)
        outFh.write(['Assay', 'Filename', 'IGV_index'])
        outFh.write([sampleName, 'None', 1])

    def __createExtractedFileFromShortReport(self, fileName, sampleName):
        outFileName = os.path.join(os.path.dirname(fileName),
                                   sampleName + '_extracted.txt')
        header = ReadFileAtOnceParser(fileName, bufferSize=1).getSplittedLine()
        xIdx = header.index('X')
        yIdx = header.index('Y')
        cmd = 'cut -f 1-3,%d,%d %s > %s' % (xIdx, yIdx, fileName, outFileName)
        Utilities.mySystem(cmd)

    def _run(self, splitDir, beadchip):
        sampleFile = os.path.join(splitDir, 'sample_names.txt')
        outFh = CsvFileWriter(sampleFile)
        outFh.write(['Assay', 'Filename', 'IGV_index'])
        fileNb = 1
        for splitFile in glob.glob(os.path.join(splitDir, '*.txt')):
            if 'sample_names.txt' in splitFile or 'tQN_parameters.txt' in \
               splitFile or '_extracted.txt' in splitFile:
                continue
            sampleName = os.path.basename(splitFile).split('_')[-1]
            sampleName = sampleName.split('.')[0]
            outFh.write([sampleName, os.path.basename(splitFile), fileNb])
            Utilities()._runFunc(self.__createExtractedFileFromShortReport,
                                 [splitFile, sampleName], splitFile)
            fileNb += 1
        outFh.close()
        outputDir = os.path.join(splitDir, 'normalized')
        Utilities.mySystem('mkdir -p %s' % outputDir)
        self.__linkTQNfiles(splitDir)
        tQNdir = os.path.join(self.__binDir, 'tQN')
        cmd = 'cd %s && perl %s/tQN_normalize_samples.pl --beadchip=%s \
        --input_directory=%s --output_directory=%s' % \
            (splitDir, tQNdir, beadchip, splitDir, outputDir)
        Utilities.mySystem(cmd)
        return outputDir

    def __linkTQNfiles(self, targetDir):
        tQNdir = os.path.join(self.__binDir, 'tQN')
        for fileName in [os.path.join(tQNdir, 'lib'),
                         os.path.join(tQNdir, 'tQN.R')]:
            os.system('ln -s %s %s' % (fileName, targetDir))

    def _normalizeData(self, fileName, targetDir=None, beadchip=None):
        if not beadchip:
            beadchip = self._getBeadchipNameFromIlluminaReportFile(fileName)
        sampleName = os.path.basename(fileName).split('_')[0]
        sampleDir = os.path.join(targetDir, sampleName)
        Utilities.mySystem('mkdir -p %s' % sampleDir)
        os.system('ln -s %s %s' % (fileName, sampleDir))
        self.__createSampleFile(sampleName, sampleDir)
        outputDir = os.path.join(sampleDir, 'normalized')
        Utilities.mySystem('mkdir -p %s' % outputDir)
        self.__linkTQNfiles(sampleDir)
        tQNdir = os.path.join(self.__binDir, 'tQN')
        # print os.environ['HOSTNAME']
        cmd = 'cd %s && perl %s/tQN_normalize_samples.pl --beadchip=%s \
        --input_directory=%s --output_directory=%s' % \
            (sampleDir, tQNdir, beadchip, sampleDir, outputDir)
        Utilities.mySystem(cmd)
        return

    def _getSortedFinalReportListFromDir(self, dirName):
        fileList = glob.glob(os.path.join(dirName, '*txt'))
        fileList = [(int(os.path.basename(fileName).split('FinalReport')[-1].
                         split('.')[0]), fileName) for fileName in fileList
                    if 'FinalReport' in fileName]
        fileList.sort()
        return fileList

    def __checkNormalizedFile(self, fileName, finalReportFile):
        sampleName = os.path.basename(fileName).split('_')[0]
        normalizedFile = os.path.join(os.path.dirname(fileName), sampleName,
                                      'normalized', sampleName + '_tQN.txt')
        if not os.path.isfile(normalizedFile):
            os.system('rm %s_tQN_norm_done' % finalReportFile)
            print normalizedFile, finalReportFile
            # sys.exit(0)

    def process(self, dirName, targetDir):
        if os.path.basename(targetDir.rstrip(os.path.sep)) != 'extracted':
            targetDir = os.path.join(targetDir, 'extracted')
        cluster = guessHpc()
        Utilities.mySystem('mkdir -p %s' % targetDir)
        outFh = CsvFileWriter(os.path.join(targetDir, 'sample_names.txt'))
        outFh.write(['Assay', 'Filename', 'IGV_index'])
        reportFileList = self._getSortedFinalReportListFromDir(dirName)
        for reportNb, finalReportFile in reportFileList:
            # outFileName = self.__formatFinalReportFile(fileName, targetDir)
            splitFile = finalReportFile + '_tQN_split'
            res = self._getFhSampleNameHeaderAndOutFileNameFromFile(
                finalReportFile, targetDir)
            fh, sampleName, header, outFileName = res
            self.__checkNormalizedFile(outFileName, finalReportFile)
            cmdList = [(finalReportFile, splitFile,
                        'python %s -p formatTQN -f [input] -t %s' %
                        (os.path.abspath(__file__), targetDir)),
                       (splitFile, finalReportFile + '_tQN_norm',
                        Cmd('python %s -p tQN -f %s -t %s -b %s' %
                            (os.path.abspath(__file__), outFileName,
                             targetDir, self.__binDir), memory=8))]
            ProcessFileFromCluster()._runCmdList(cmdList, finalReportFile,
                                                 cluster)
            outFh.write([sampleName, os.path.basename(outFileName),
                         reportNb])
            # break


class DefaultPloidyDict(dict):

    def has_key(self, key):
        return True

    def __contains__(self, key):
        return self.has_key(key)
    
    def __getitem__(self, key):
        return 2


class RunAscat:

    def __init__(self, binDir=None, rLibDir=None):
        self.__binDir = binDir
        self.__rLibDir = rLibDir

    def __createGroupFilesAndGetHeaderAndPloidyIdx(self, ploidyFile,
                                                   targetDir):
        fh = ReadFileAtOnceParser(ploidyFile)
        header = [columnName.lower() for columnName in fh.getSplittedLine()]
        ploidyIdx = header.index('ploidy')
        outFhDict = {}
        groupSetDict = defaultdict(set)
        for columnName in header[ploidyIdx + 1:]:
            outFileName = '_%s.txt' % columnName.replace(' ', '')
            outFileName = FileNameGetter(ploidyFile).get(outFileName)
            outFileName = os.path.join(targetDir,
                                       os.path.basename(outFileName))
            outFh = CsvFileWriter(outFileName)
            outFh.write(['sample', 'ploidy', 'group'])
            outFhDict[columnName] = outFh
        for splittedLine in fh:
            for i, columnName in enumerate(header[ploidyIdx + 1:]):
                outFh = outFhDict[columnName]
                group = splittedLine[ploidyIdx + 1 + i]
                groupSetDict[columnName].add(group)
                outFh.write([splittedLine[0], splittedLine[ploidyIdx], group])
        for columnName in header[ploidyIdx + 1:]:
            print 'Column "%s" has %d values' % (columnName,
                                                 len(groupSetDict[columnName]))
        return header, ploidyIdx, groupSetDict,\
            dict([[columnName, outFh._fileName] for columnName, outFh in
                  outFhDict.iteritems()])

    def _createDendrogramForEachFeature(self, ploidyFile, targetDir,
                                        windowSize, percentage,
                                        segmentFile, chrFile):
        Utilities.mySystem('mkdir -p %s' % targetDir)
        dumpFileName = os.path.join(targetDir, os.path.basename(ploidyFile))
        dumpFileName = FileNameGetter(dumpFileName).get('pyDump')
        header, ploidyIdx, groupSetDict, outFhDict = Utilities.\
            getFunctionResultWithCache(
                dumpFileName,
                self.__createGroupFilesAndGetHeaderAndPloidyIdx, ploidyFile,
                targetDir)
        # filter segment file to keep only samples with BCLC Staging
        # information add option in createDendrogram to replace null value in
        # group column with a default value add option in createDendrogram to
        # replace sample name with group value
        for columnName in header[ploidyIdx + 1:]:
            print 'Plotting dendrogram for column "%s"' % columnName
            groupSet = groupSetDict[columnName]
            useShape = False
            if len(groupSet) <= 5:
                useShape = True
            # obj = aCNViewer(windowSize, percentage, self.__binDir, useShape)
            # groupDict, group2Dict, colorDict, shapeDict, shapeDict2 =
            # obj._getGroupAndColorCodeDictFromFile(ploidyFile)
            # obj._createDendrogram(matrixFile, groupDict, colorDict,
            # shapeDict, ploidyFile2, shapeDict2, coeff, keyword =
            # columnName.replace(' ', ''))
            aCNViewer(windowSize, percentage, self.__binDir, useShape).\
                process(segmentFile, chrFile, targetDir, outFhDict[
                    columnName], histogram=0, merge=1, dendrogram=1,
                groupColumnName='test',
                keyword=columnName.replace(' ', ''),
                defaultGroupValue='UNKNOWN')
            # break

    def __getSampleDictFromFh(self, fh, sampleIdx):
        sampleDict = {}
        for splittedLine in fh:
            sampleDict[splittedLine[sampleIdx]] = splittedLine
        return sampleDict

    def _mergePloidyFileWithSampleInfoFile(self, ploidyFile, sampleFile,
                                           sampleAliasFile):
        sampleDict = self._getSampleDictFromFile(sampleAliasFile)
        outFh = CsvFileWriter(FileNameGetter(ploidyFile).get('_merged.txt'))
        fh = ReadFileAtOnceParser(ploidyFile)
        sampleHeader, sampleFh, sampleIdx = \
            self._getHeaderFhAndSampleIdxForSampleFile(sampleFile)
        sampleInfoDict = self.__getSampleDictFromFh(sampleFh, sampleIdx)
        header = fh.getSplittedLine() + sampleHeader
        outFh.write(header)
        for splittedLine in fh:
            sampleName = splittedLine[sampleIdx]
            sampleAlias = sampleDict[sampleName]
            outFh.write(splittedLine + sampleInfoDict[sampleAlias])

    def __getBuildFromPfbFile(self, fileName):
        for part in os.path.basename(fileName).split('.'):
            if part[:2] == 'hg':
                return part

    def __getProbeToPosDictFromFile(self, fileName):
        probeDict = {}
        for splittedLine in ReadFileAtOnceParser(fileName):
            chrName, start, end, probeName = splittedLine
            probeDict[probeName] = chrName.replace('chr', ''), int(end)
        return probeDict

    def _createFileWithUpdatedPositions(self, fileName, rawProfileFile,
                                        targetBuild=None):
        fileExt = Utilities.getFileExtension(fileName)
        if not targetBuild:
            targetBuild = self.__getBuildFromPfbFile(fileName)
        liftedOverFile = Utilities.getFunctionResultWithCache(FileNameGetter(
            rawProfileFile).get('_%s.pyDump' %
                                targetBuild), self._liftOverRawProbeFile,
            rawProfileFile,
            targetBuild)
        probeDict = Utilities.getFunctionResultWithCache(FileNameGetter(
            liftedOverFile).get('pyDump2'), self.__getProbeToPosDictFromFile,
            liftedOverFile)
        outFileName = FileNameGetter(fileName).get('_liftedOver.%s' % fileExt)
        outFh = CsvFileWriter(outFileName)
        fh = ReadFileAtOnceParser(fileName)
        outFh.write(fh.getSplittedLine())
        for splittedLine in fh:
            probeName, chrName, pos = splittedLine[:3]
            probePos = probeDict.get(probeName)
            if not probePos:
                print 'Passing probe %s' % probeName
                continue
            chrName, pos = probePos
            splittedLine[1] = chrName
            splittedLine[2] = pos
            outFh.write(splittedLine)

    def __getChrNameFromStr(self, chrName):
        if chrName[:3] != 'chr':
            chrName = 'chr' + chrName
        return chrName

    def _liftOverRawProbeFile(self, fileName, targetBuild):
        fh = ReadFileAtOnceParser(fileName)
        for splittedLine in fh:
            if splittedLine[0][0] != '#':
                header = splittedLine
                break
        outFileName = FileNameGetter(fileName).get('bed')
        outFh = CsvFileWriter(outFileName)
        probeIdx = header.index('ID')
        buildIdx = header.index('Genome Version')
        chrIdx = header.index('Chromosome')
        posIdx = header.index('Physical Position')
        buildName = None
        for splittedLine in fh:
            if not splittedLine[chrIdx]:
                break
            currentBuild = splittedLine[buildIdx]
            if not buildName:
                buildName = currentBuild
            elif buildName != currentBuild:
                raise NotImplementedError('Inconsistent build as "%s" and \
                "%s" were found. Current line = [%s]' % (buildName,
                                                         currentBuild,
                                                         str(splittedLine)))
            probeName = splittedLine[0]
            chrName = splittedLine[chrIdx]
            pos = int(splittedLine[posIdx])
            outFh.write([self.__getChrNameFromStr(
                chrName), pos - 1, pos, probeName])
        outFh.close()
        from liftOver import LiftOver
        return LiftOver(self.__binDir).process(outFileName, (buildName,
                                                             targetBuild))

    def __getApproximatedPloidy(self, ploidy):
        if ploidy % 2 >= 1:
            ploidy = int(ploidy) + 1
        else:
            ploidy = int(ploidy)
        return ploidy

    def _getSampleToGroupDictFromFile(self, sampleFile, columnName,
                                      sampleAliasDict=None):
        header, fh, sampleIdx = self._getHeaderFhAndSampleIdxForSampleFile(
            sampleFile)
        sampleToGroupDict = {}
        groupIdx = header.index(columnName)
        sampleAliasColumnName = 'sampleAlias'
        sampleAliasIdx = None
        if sampleAliasColumnName in header:
            sampleAliasIdx = header.index(sampleAliasColumnName)
        for splittedLine in fh:
            sampleName = splittedLine[0]
            group = splittedLine[groupIdx].strip()
            if not group:
                continue
            if sampleAliasDict:
                if sampleName not in sampleAliasDict:
                    print 'Passing sample %s because no alias' % sampleName
                    continue
                sampleName = sampleAliasDict[sampleName]
            sampleToGroupDict[sampleName] = group
            if sampleAliasIdx is not None:
                sampleToGroupDict[splittedLine[sampleAliasIdx]] = group
        return sampleToGroupDict

    def _getHeaderFhAndSampleIdxForSampleFile(self, sampleFile):
        fh = ReadFileAtOnceParser(sampleFile)
        sampleIdx = header = None
        while fh.hasLinesLeft():
            splittedLine = fh.getSplittedLine()
            splittedLine2 = [columnName.lower() for columnName in splittedLine]
            for sampleColumnName in ['sample', 'sample id']:
                if sampleColumnName in splittedLine2:
                    header = splittedLine
                    sampleIdx = splittedLine2.index(sampleColumnName)
                    break
            if header:
                break
        return header, fh, sampleIdx

    def _createPloidyFile(self, fileName, sampleAliasFile, sampleFile,
                          columnName):
        sampleDict = None
        if sampleAliasFile:
            sampleDict = self._getSampleDictFromFile(sampleAliasFile)
        sampleToGroupDict = self._getSampleToGroupDictFromFile(
            sampleFile, columnName, sampleDict)
        # print sampleToGroupDict
        outFh = CsvFileWriter(FileNameGetter(fileName).get('_approx.txt'))
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        if header[1] == 'x':
            header[1] = 'ploidy'
        outFh.write(header + ['group'])
        for splittedLine in fh:
            sampleName = splittedLine[0]
            # if not sampleToGroupDict.has_key(sampleName):
            # print 'Passing sample %s' % sampleName
            # continue
            ploidy = float(splittedLine[1])
            lineToWrite = [splittedLine[0], self.__getApproximatedPloidy(
                ploidy), sampleToGroupDict.get(sampleName, '')]
            if len(lineToWrite) != len(header):
                raise NotImplementedError
            outFh.write(lineToWrite)

    def _getSampleDictFromFile(self, sampleAliasFile):
        sampleDict = {}
        fh = ReadFileAtOnceParser(sampleAliasFile)
        for splittedLine in fh:
            sampleName1, sampleName2 = splittedLine
            sampleDict[sampleName1] = sampleName2
            sampleDict[sampleName2] = sampleName1
        return sampleDict

    def __getSampleDictFromClinicalFile(self, sampleFile):
        header, fh, sampleIdx = self._getHeaderFhAndSampleIdxForSampleFile(
            sampleFile)
        sampleDict = {}
        sampleAliasIdx = None
        if 'sampleAlias' in header:
            sampleAliasIdx = header.index('sampleAlias')
        for splittedLine in fh:
            sampleName = splittedLine[sampleIdx]
            if sampleAliasIdx is not None:
                sampleAlias = splittedLine[sampleAliasIdx]
                sampleDict[sampleName] = sampleAlias
                sampleDict[sampleAlias] = sampleName
            else:
                sampleDict[sampleName] = sampleName
        return sampleDict

    def __getTumorSampleListFromFile(self, platform, tumorSampleFile,
                                     sampleAliasFile=None):
        if sampleAliasFile:
            sampleDict = self._getSampleDictFromFile(sampleAliasFile)
        else:
            sampleDict = self.__getSampleDictFromClinicalFile(tumorSampleFile)
            # print 'sampleDict = ', sampleDict
        header, fh, sampleIdx = self._getHeaderFhAndSampleIdxForSampleFile(
            tumorSampleFile)
        tumorSampleList = []
        for splittedLine in fh:
            sampleName = splittedLine[sampleIdx]
            if sampleName not in sampleDict:
                print 'Passing SAMPLE %s' % sampleName
                continue
            sampleName = sampleDict[sampleName]
            tumorSampleList.append(sampleName)
        return tumorSampleList

    def __getTumorAndNormalLogRandBafIdxDictFromFile(self, lrrBafFile,
                                                     tumorSampleList):
        fh = ReadFileAtOnceParser(lrrBafFile, 1)
        header = fh.getSplittedLine()
        idxDict = {'Normal': defaultdict(list), 'Tumor': defaultdict(list)}
        currentTumorSampleList = []
        normalSampleList = []
        for i, colName in enumerate(header):
            if i <= 2:
                continue
            sampleName = colName.split('.')[0]
            # print i, sampleName
            if sampleName in tumorSampleList:
                sampleType = 'Tumor'
                if sampleName not in currentTumorSampleList:
                    currentTumorSampleList.append(sampleName)
            else:
                sampleType = 'Normal'
                if sampleName not in normalSampleList:
                    normalSampleList.append(sampleName)
            dataType = None
            if '.Log R Ratio' in colName:
                dataType = 'LogR'
            elif '.B Allele Freq' in colName:
                dataType = 'BAF'
            if not dataType:
                continue
            idxDict[sampleType][dataType].append(i)
        return idxDict, currentTumorSampleList, normalSampleList

    def __getTumorSampleListFromLrrBafFile(self, fileName):
        fh = ReadFileAtOnceParser(fileName, bufferSize=1)
        header = fh.getSplittedLine()
        sampleSet = set([columnName.split('.')[0]
                         for columnName in header[3:]])
        return list(sampleSet)

    def __createRscript(self, sampleFile, lrrBafFile, sampleAliasFile, gcFile,
                        platform):
        lrrBafFile = os.path.abspath(lrrBafFile)
        gcFile = os.path.abspath(gcFile)
        if sampleFile:
            sampleFile = os.path.abspath(sampleFile)
            if sampleAliasFile:
                sampleAliasFile = os.path.abspath(sampleAliasFile)
            tumorSampleList = self.__getTumorSampleListFromFile(
                platform, sampleFile, sampleAliasFile)
        else:
            tumorSampleList = self.__getTumorSampleListFromLrrBafFile(
                lrrBafFile)
        print '%d tumor samples' % len(tumorSampleList)
        # print tumorSampleList
        idxDict, tumorSampleList, normalSampleList = \
            self.__getTumorAndNormalLogRandBafIdxDictFromFile(
                lrrBafFile, tumorSampleList)
        # print idxDict
        snpPosFile = os.path.join(os.path.dirname(
            lrrBafFile), 'SNPpos_%s.txt' %
            os.path.basename(lrrBafFile).split('.')[0])
        cmd = 'cut -f 1-3 %s > %s' % (lrrBafFile, snpPosFile)
        Utilities()._runFunc(Utilities.mySystem, [cmd], snpPosFile)
        print len(tumorSampleList), len(normalSampleList),\
            len(idxDict['Tumor']['LogR']), len(idxDict['Tumor']['BAF']),\
            len(idxDict['Normal']['LogR']), len(idxDict['Normal']['BAF'])
        baseName = '.'.join(lrrBafFile.split('.')[:-1])
        libStr = R(libDir=self.__rLibDir).getLibStr()
        rStr = '''X11.options(colortype="pseudo.cube")

baseName <- "%s"
lrrbaf = read.table("%s", header = T, sep = "\\t", row.names=1)
SNPpos = read.table("%s", header=T, sep="\\t", row.names=1)

normalSampleList <- %s
tumorSampleList <- %s

isMatchedNormalTumor <- length(normalSampleList) == length(tumorSampleList)
print(paste(c("isMatchedNormalTumor=", isMatchedNormalTumor)))

tumorLogRidxList <- %s
tumorBAFidxList <- %s
normalLogRidxList <- %s
normalBAFidxList <- %s

Tumor_LogR = lrrbaf[rownames(SNPpos),tumorLogRidxList,drop=F]
colnames(Tumor_LogR) = tumorSampleList

Tumor_BAF = lrrbaf[rownames(SNPpos),tumorBAFidxList,drop=F]
colnames(Tumor_BAF) = tumorSampleList

if (isMatchedNormalTumor){
    Normal_LogR = lrrbaf[rownames(SNPpos),normalLogRidxList,drop=F]
    colnames(Normal_LogR) = normalSampleList

    Normal_BAF = lrrbaf[rownames(SNPpos),normalBAFidxList,drop=F]
    colnames(Normal_BAF) = normalSampleList
}

# replace 2's by NA
Tumor_BAF[Tumor_BAF==2]=NA

# Tumor_LogR: correct difference between copy number only probes and other
# probes
CNprobes = substring(rownames(SNPpos),1,2)=="CN"

Tumor_LogR[CNprobes,1] = Tumor_LogR[CNprobes,1]-mean(Tumor_LogR[CNprobes,1],
na.rm=T)
Tumor_LogR[!CNprobes,1] = Tumor_LogR[!CNprobes,1]-mean(Tumor_LogR[!CNprobes,1],
na.rm=T)

if (isMatchedNormalTumor) {
    Normal_BAF[Normal_BAF==2]=NA
    Normal_LogR[CNprobes,1] = Normal_LogR[CNprobes,1]-mean(
    Normal_LogR[CNprobes,1],na.rm=T)
    Normal_LogR[!CNprobes,1] = Normal_LogR[!CNprobes,1]-mean(
    Normal_LogR[!CNprobes,1],na.rm=T)
}

# limit the number of digits:
Tumor_LogR = round(Tumor_LogR,4)

file.tumor.LogR <- paste(baseName, ".tumor.LogR.txt", sep="")
file.tumor.BAF <- paste(baseName, ".tumor.BAF.txt", sep="")
write.table(cbind(SNPpos,Tumor_BAF),file.tumor.BAF,sep="\t",row.names=T,
col.names=NA,quote=F)
write.table(cbind(SNPpos,Tumor_LogR),file.tumor.LogR,sep="\t",row.names=T,
col.names=NA,quote=F)

if (isMatchedNormalTumor){
    Normal_LogR = round(Normal_LogR,4)
    file.normal.LogR <- paste(baseName, ".normal.LogR.txt", sep="")
    file.normal.BAF <- paste(baseName, ".normal.BAF.txt", sep="")
    write.table(cbind(SNPpos,Normal_BAF),file.normal.BAF,sep="\t",row.names=T,
    col.names=NA,quote=F)
    write.table(cbind(SNPpos,Normal_LogR),file.normal.LogR,sep="\t",
    row.names=T,col.names=NA,quote=F)
}

# run ASCAT functions

library(ASCAT%s)
if (isMatchedNormalTumor){
    ascat.bc <- ascat.loadData(file.tumor.LogR, file.tumor.BAF,
    file.normal.LogR, file.normal.BAF, chrs=1:22)
} else {
    ascat.bc <- ascat.loadData(file.tumor.LogR, file.tumor.BAF, chrs=1:22)
}

# GC correction
ascat.bc <- ascat.GCcorrect(ascat.bc, "%s")
ascat.plotRawData(ascat.bc)

if (isMatchedNormalTumor){
    ascat.bc <- ascat.aspcf(ascat.bc)
} else {
    ascat.gg = ascat.predictGermlineGenotypes(ascat.bc, "%s")
    ascat.bc = ascat.aspcf(ascat.bc,ascat.gg=ascat.gg)
}

ascat.plotSegmentedData(ascat.bc)
save(ascat.bc, baseName, file = "%s")

ascat.output <- ascat.runAscat(ascat.bc)
save(ascat.output, file = "%s")

# save ASCAT results

write.table(ascat.output$segments, file=paste(baseName,".segments.txt",sep=""),
sep="\\t", quote=F, row.names=F)
write.table(ascat.output$aberrantcellfraction, file=paste(baseName,".acf.txt",
sep=""), sep="\\t", quote=F, row.names=F)
write.table(ascat.output$ploidy, file=paste(baseName,".ploidy.txt",sep=""),
sep="\\t", quote=F, row.names=F)

# export "aberrantcellfraction" and "goodnessOfFit"
q <- rbind(ascat.output$aberrantcellfraction, ascat.output$goodnessOfFit, ascat.output$psi, ascat.output$ploidy)
rownames(q) <- c("aberrantcellfraction", "goodnessOfFit", "psi", "ploidy")
write.table(q,paste(baseName,".ascatInfo.txt",sep=""),row.names=T,na="NA",quote= FALSE,sep="\t",col.names=NA)

save.image(paste(baseName,".RData",sep=""))
''' % (baseName, lrrBafFile, snpPosFile, R()._getStrFromList(normalSampleList),
            R()._getStrFromList(tumorSampleList),
            R()._getStrFromList(idxDict['Tumor']['LogR']),
            R()._getStrFromList(idxDict['Tumor']['BAF']),
            R()._getStrFromList(idxDict['Normal']['LogR']),
            R()._getStrFromList(idxDict['Normal']
                                ['BAF']), libStr, gcFile, platform,
            FileNameGetter(lrrBafFile).get('Ro'),
            FileNameGetter(lrrBafFile).get('_ASCAT.Ro'))
        rFileName = FileNameGetter(lrrBafFile).get('R')
        outFh = CsvFileWriter(rFileName)
        outFh.write(rStr)
        return rFileName, baseName + '.segments.txt'

    def __createCelFileListFile(self, celDirName, targetDir):
        celFilePattern = os.path.join(celDirName, '*.cel')
        celFileList = glob.glob(os.path.join(celDirName, '*.cel'))
        if not celFileList:
            celFilePattern2 = os.path.join(celDirName, '*.cel.gz')
            celFileList = glob.glob(celFilePattern2)
            if celFileList:
                cmd = 'gunzip %s' % celFilePattern2
                Utilities.mySystem(cmd)
                celFileList = glob.glob(celFilePattern)
        if not celFileList:
            raise NotImplementedError(
                'Could not find CEL files in dir %s' % celDirName)
        targetFileName = os.path.join(targetDir, 'list.nsp')
        cmd = 'echo "cel_files" > %s' % targetFileName
        Utilities.mySystem(cmd)
        cmd = 'ls %s >> %s' % (celFilePattern, targetFileName)
        Utilities.mySystem(cmd)
        return targetFileName

    def __getAptBinFolder(self):
        dirList = glob.glob(os.path.join(self.__binDir, 'apt-*', 'bin'))
        if len(dirList) != 1:
            raise NotImplementedError(
                'Could not find APT binary folder in %s' % self.__binDir)
        return dirList[0]

    def __getCdfAndChrXfileFromDir(self, libDir):
        fileList = glob.glob(os.path.join(libDir, '*.cdf'))
        if len(fileList) != 1:
            raise NotImplementedError('Expecting one cdf file in dir %s but \
found %d:\n%s' % (libDir, len(fileList), '\n'.join(fileList)))
        cdfFile = fileList[0]
        fileList = glob.glob(os.path.join(libDir, '*.chrx'))
        if len(fileList) != 1:
            raise NotImplementedError('Expecting one chrx file in dir %s but \
found %d:\n%s' % (libDir, len(fileList), '\n'.join(fileList)))
        chrXfile = fileList[0]
        return cdfFile, chrXfile

    def __getGw6LibDirFromPlatform(self, gw6Dir, platform):
        if platform in ["Affy250k_sty", "Affy250k_nsp", "Affy500k"]:
            libDir = 'lib500k'
        elif platform == 'AffySNP6':
            libDir = 'lib'
        else:
            raise NotImplementedError('Unsupported plaform "%s"' % platform)
        libDir = os.path.join(gw6Dir, libDir)
        if not os.path.isdir(libDir):
            raise NotImplementedError('libDir %s does not exist...' % libDir)
        return libDir

    def __getFilteredFileList(self, fileList, platform):
        if len(fileList) > 1:
            if platform == 'Affy250k_sty':
                fileList = [
                    fileName for fileName in fileList if '.sty.' in fileName]
            elif platform == 'Affy250k_nsp':
                fileList = [
                    fileName for fileName in fileList if '.nsp.' in fileName]
            else:
                raise NotImplementedError('Unhandled platform "%s"' % platform)
        if len(fileList) != 1:
            raise NotImplementedError(
                'Could not find targetSketchFile in %s' % gw6LibDir)
        return fileList

    def __getTargetSketchFileFromLibDirAndPlatform(self, gw6LibDir, platform):
        fileList = glob.glob(os.path.join(
            gw6LibDir, '*.normalization-target.txt'))
        fileList = self.__getFilteredFileList(fileList, platform)
        return fileList[0]

    def __getGenoClusterPfbFileTargetSketchFileAndBinDirFromDir(self, gw6Dir,
                                                                platform):
        if platform not in ["Affy250k_sty", "Affy250k_nsp", "AffySNP6"]:
            raise NotImplementedError('Unsupported platform "%s"' % platform)
        gw6LibDir = self.__getGw6LibDirFromPlatform(gw6Dir, platform)
        fileList = glob.glob(os.path.join(gw6LibDir, '*.genocluster'))
        fileList = self.__getFilteredFileList(fileList, platform)
        genoClusterFile = fileList[0]
        fileList = glob.glob(os.path.join(gw6LibDir, '*.pfb'))
        if len(fileList) > 1:
            fileList = [
                fileName for fileName in fileList if 'liftedOver' in fileName]
        if len(fileList) != 1:
            raise NotImplementedError('Expecting one cdf file in dir %s but \
found %d:\n%s' % (gw6LibDir, len(fileList), '\n'.join(fileList)))
        pfbFile = fileList[0]
        binDir = os.path.join(gw6Dir, 'bin')
        if not os.path.isdir(binDir):
            raise NotImplementedError('gw6 bin dir %s not found' % binDir)
        targetSketchFileName = self.__getTargetSketchFileFromLibDirAndPlatform(
            gw6LibDir, platform)
        return genoClusterFile, pfbFile, targetSketchFileName, binDir

    def _runPennCnvAndGetLrrBafFile(self, celDirName, libDir, gw6Dir,
                                    platform, targetDir):
        if not os.path.isdir(self.__binDir):
            raise NotImplementedError(
                'Please specify bin dir where Affymetrix Power Tools (APT) is \
installed. The bin folder should contain a folder named "apt-*" which \
represents the APT folder. This APT folder should contain a bin folder \
containing all the necessary binaries.')
        cdfFile, chrXfile = self.__getCdfAndChrXfileFromDir(libDir)
        aptBinDir = self.__getAptBinFolder()
        celFileListFile = self.__createCelFileListFile(celDirName, targetDir)
        if not targetDir:
            targetDir = celDirName
        targetDir = os.path.join(targetDir, 'apt_out')
        genoClusterFile, pfbFile, targetSketchFileName, \
            gw6BinDir = \
            self.__getGenoClusterPfbFileTargetSketchFileAndBinDirFromDir(
                gw6Dir, platform)

        print 'Step 1.1: Extracting genotypes from CEL files'
        cmd = '%s/apt-probeset-genotype -c %s --chrX-snps %s --out-dir %s \
--cel-files %s' % (aptBinDir, cdfFile, chrXfile, targetDir, celFileListFile)
        Utilities()._runFunc(Utilities.mySystem, [
            cmd], os.path.join(targetDir, 'step1.1'))

        print 'Step 1.2: Allele-specific signal extraction from CEL files'
        cmd = '%s/apt-probeset-summarize --cdf-file %s --out-dir %s \
--cel-files %s -a quant-norm.sketch=50000,pm-only,med-polish,\
expr.genotype=true --target-sketch %s' % (aptBinDir, cdfFile, targetDir,
                                          celFileListFile,
                                          targetSketchFileName)
        Utilities()._runFunc(Utilities.mySystem, [
            cmd], os.path.join(targetDir, 'step1.2'))

        print 'Step 1.4: LRR and BAF calculation'
        quantNormFile = os.path.join(
            targetDir, 'quant-norm.pm-only.med-polish.expr.summary.txt')
        if not os.path.isfile(quantNormFile):
            raise NotImplementedError(
                'quantNormFile %s not found' % quantNormFile)
        lrrBafFile = os.path.join(targetDir, 'lrr_baf.txt')
        cmd = '%s/normalize_affy_geno_cluster.pl %s %s -locfile %s -out %s' % (
            gw6BinDir, genoClusterFile, quantNormFile, pfbFile, lrrBafFile)
        Utilities()._runFunc(Utilities.mySystem, [
            cmd], os.path.join(targetDir, 'step1.4'))
        return lrrBafFile

    def __checkAndInstallRpackagesIfNecessary(self, rDir):
        # RColorBrewer
        if R(rDir).isPackageInstalled('ASCAT'):
            return
        fileName = 'ASCAT.tar.gz'
        for cmd in ['wget https://github.com/Crick-CancerGenomics/ascat/archive\
/master.zip', 'unzip master.zip && rm master.zip',
                    'cd ascat-master && tar czf ../%s ASCAT && cd .. && \
rm -rf ascat-master' % fileName]:
            Utilities.mySystem(cmd)
        try:
            R(rDir, libDir=self.__rLibDir).installPackage(fileName)
        except:
            print 'You may need to add option "--rLibDir DIRNAME" in order to \
specify R package installation folder.'
            raise
        Utilities.mySystem('rm %s' % fileName)

    def _getFhHeaderAndLineNbForIlluminaReport(self, fileName):
        lineNb = 0
        fh = ReadFileAtOnceParser(fileName)
        for splittedLine in fh:
            lineNb += 1
            if splittedLine[0] == '[Data]':
                break
        return fh, fh.getSplittedLine(), lineNb

    def _getSampleListFromSnpArrayDataFile(self, fileName):
        fh, header, lineNb = RunAscat()._getFhHeaderAndLineNbForIlluminaReport(
            fileName)
        columnName = 'B Allele Freq'
        print 'Header', header
        if columnName not in header:
            return []
        sampleList = []
        if 'Sample ID' in header:
            outSampleFile = FileNameGetter(fileName).get('_sample.txt')
            sampleIdx = header.index('Sample ID')
            cmd = "awk 'NR >= %d' <(cut -f %d %s) | uniq > %s" % (
                lineNb + 2, sampleIdx + 1, fileName, outSampleFile)
            scriptName = FileNameGetter(fileName).get('sh')
            Utilities.mySystem(cmd, scriptName=scriptName)
            fh = ReadFileAtOnceParser(outSampleFile)
            for splittedLine in fh:
                sampleList.append(splittedLine[0])
        else:
            sampleFile = os.path.join(
                os.path.dirname(fileName), 'Sample_Map.txt')
            fh = ReadFileAtOnceParser(sampleFile)
            header = fh.getSplittedLine()
            sampleIdx = header.index('ID')
            for splittedLine in fh:
                sampleList.append(splittedLine[sampleIdx])
        return sampleList

    def __getLrrBafFileHeaderForSampleList(self, sampleList):
        header = ['Name', 'Chr', 'Position']
        for sampleName in sampleList:
            header += ['%s.Log R Ratio' %
                       sampleName, '%s.B Allele Freq' % sampleName]
        return header

    def _getSnpDictFromFile(self, fileName):
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        nameIdx = chrIdx = posIdx = None
        if 'Name' in header:
            nameIdx = header.index('Name')
            chrIdx = header.index('Chr')
            if 'Position' in header:
                posIdx = header.index('Position')
            else:
                posIdx = header.index('MapInfo')
        else:
            fh.restore(header)
        snpDict = {}
        for splittedLine in fh:
            if nameIdx is not None:
                snpName = splittedLine[nameIdx]
                chrName = splittedLine[chrIdx]
                pos = int(splittedLine[posIdx])
            else:
                snpName, chrName, pos = splittedLine[:3]
                pos = int(pos)
            snpDict[snpName] = chrName, pos
        return snpDict

    def __getLogRandBafDictFromIlluminaReportFile(self, fileName,
                                                  sampleList=None,
                                                  targetDir=None):
        print 'Processing file "%s"' % fileName
        fh, header, lineNb = self._getFhHeaderAndLineNbForIlluminaReport(
            fileName)
        dumpFileName = FileNameGetter(fileName).get('pyDump')
        if targetDir:
            dumpFileName = os.path.join(targetDir, os.path.basename(dumpFileName))
        currentSampleList = Utilities.getFunctionResultWithCache(
            dumpFileName, self._getSampleListFromSnpArrayDataFile, fileName)
        if sampleList is None:
            sampleList = []
        # GSM248782.cel.Log R Ratio       GSM248782.cel.B Allele Freq
        finalSampleList = [sampleName for sampleName in currentSampleList if
                           not sampleList or sampleName in sampleList]
        snpDict = defaultdict(dict)
        snpNameIdx = header.index('SNP Name')
        sampleIdIdx = header.index('Sample ID')
        logRidx = header.index('Log R Ratio')
        bafIdx = header.index('B Allele Freq')
        nb = 0
        for splittedLine in fh:
            nb += 1
            logR = float(splittedLine[logRidx])
            baf = float(splittedLine[bafIdx])
            sampleName = splittedLine[sampleIdIdx]
            if sampleName in finalSampleList:
                snpDict[splittedLine[snpNameIdx]][sampleName] = logR, baf
            if nb % 1000000 == 0:
                print nb, Utilities.getTimeString()
        return snpDict, finalSampleList

    def __writeLrrBafFileFromDict(self, snpDict, snpFile, sampleList,
                                  outFileName):
        snpPosDict = self._getSnpDictFromFile(snpFile)
        outFh = CsvFileWriter(outFileName)
        lineToWrite = self.__getLrrBafFileHeaderForSampleList(sampleList)
        outFh.write(lineToWrite)
        for snpName in snpDict:
            if snpName not in snpPosDict:
                print 'Passing SNP %s' % snpName
                continue
            chrName, pos = snpPosDict[snpName]
            lineToWrite = [snpName, chrName, pos]
            for sampleName in sampleList:
                logR, baf = snpDict[snpName][sampleName]
                lineToWrite += [logR, baf]
            outFh.write(lineToWrite)

    def __createLrrBafFileFromNormalizedFile(self, fileName, snpFile,
                                             outFileName):
        outFh = CsvFileWriter(outFileName)
        snpPosDict = self._getSnpDictFromFile(snpFile)
        fh = ReadFileAtOnceParser(fileName)
        tQNkeyword = '.tQN '
        header = fh.getSplittedLine()
        if header[0] != 'Name' or tQNkeyword not in header[1]:
            raise NotImplementedError(
                'Header %s either does not start with "Name" of second column \
does not contain "%s"' % (header, tQNkeyword))
        outFh.write([header[0], 'Chr', 'Position'] +
                    [columnName.replace(tQNkeyword, '.') for columnName in
                     header[1:]])
        for splittedLine in fh:
            snpName = splittedLine[0]
            if snpName not in snpPosDict:
                print 'Passing SNP %s' % snpName
                continue
            chrName, pos = snpPosDict[snpName]
            outFh.write([splittedLine[0], chrName, pos] + splittedLine[1:])

    def _createLrrBafFileFromIlluminaReport(self, fileName, snpFile,
                                            targetDir=None, sampleList=None,
                                            normalize=True):
        outFileName = FileNameGetter(fileName).get(
            '_%d_lrrBaf.txt' % int(normalize))
        if normalize:
            beadchip = RunTQN(
                self.__binDir)._getBeadchipNameFromIlluminaReportFile(fileName)
            if not targetDir:
                targetDir = os.path.join(os.path.dirname(fileName), 'split')
            Utilities()._runFunc(RunTQN(self.__binDir).
                                 _splitFinalReportBySample,
                                 [fileName, targetDir, snpFile, sampleList],
                                 FileNameGetter(fileName).get('_split'))
            tQNdir = Utilities().getFunctionResultWithCache(
                FileNameGetter(fileName).get(
                    '_tQN.pyDump'), RunTQN(self.__binDir)._run, targetDir,
                beadchip)
            normalizedFileName = os.path.join(tQNdir, 'tQN_beadstudio.txt')
            Utilities()._runFunc(self.__createLrrBafFileFromNormalizedFile, [
                normalizedFileName, snpFile, outFileName], outFileName)
        else:
            snpDict, finalSampleList = \
                self.__getLogRandBafDictFromIlluminaReportFile(fileName,
                                                               sampleList,
                                                               targetDir)
            self.__writeLrrBafFileFromDict(
                snpDict, snpFile, finalSampleList, outFileName)
        return outFileName

    def __getHeaderFromFhList(self, fhList):
        headerList = [fh.getSplittedLine() for fh in fhList]
        header = headerList.pop(0)
        for currentHeader in headerList:
            header += currentHeader[3:]
        return header

    def __hasLinesLeft(self, fhList):
        hasLineLeft = False
        for fh in fhList:
            if fh.hasLinesLeft():
                return True

    def __getSnpLineFromFh(self, fh, splittedLine):
        snpLine = fh._snpDict.get(splittedLine[0])
        if snpLine:
            return snpLine
        for currentSplittedLine in fh:
            snpName = currentSplittedLine[0]
            if snpName == splittedLine[0]:
                return currentSplittedLine
            fh._snpDict[snpName] = currentSplittedLine

    def __getNextLineToWriteFromFhList(self, fhList):
        splittedLine = fhList[0].getSplittedLine()
        for fh in fhList[1:]:
            snpLine = self.__getSnpLineFromFh(fh, splittedLine)
            splittedLine += snpLine[3:]
        return splittedLine

    def __mergeLrrBafFiles(self, fileList, outFileName):
        outFh = CsvFileWriter(outFileName)
        fhList = [ReadFileAtOnceParser(fileName) for fileName in fileList]
        outFh.write(self.__getHeaderFromFhList(fhList))
        for fh in fhList[1:]:
            fh._snpDict = {}
        while self.__hasLinesLeft(fhList):
            splittedLine = self.__getNextLineToWriteFromFhList(fhList)
            outFh.write(splittedLine)

    def _createMergedIlluminaFinalReports(self, fileList, snpFile, outFileName,
                                          targetDir=None, sampleList=None,
                                          normalize=True):
        snpDict = defaultdict(dict)
        if sampleList and (isinstance(sampleList, types.StringType) and
                           os.path.isfile(sampleList)):
            if Utilities.getFileExtension(sampleList) == 'pyDump':
                sampleList = Utilities.loadCache(sampleList)
            else:
                sampleList = open(sampleList).read().strip().split()
        # sampleList = []
        fileToMergeList = []
        for fileName in fileList:
            lrrBafFile = Utilities.getFunctionResultWithCache(
                FileNameGetter(fileName).get('_%d_lrrBaf.pyDump' % int(
                    normalize)), self._createLrrBafFileFromIlluminaReport,
                fileName, snpFile, targetDir, sampleList, normalize)
            fileToMergeList.append(lrrBafFile)
            # currentSnpDict, currentSampleList =
            # Utilities.getFunctionResultWithCache(FileNameGetter(fileName).
            # get('_snpDict.pyDump'),
            # self.__getLogRandBafDictFromIlluminaReportFile, fileName)
            # currentSnpDict, currentSampleList =
            # self.__getLogRandBafDictFromIlluminaReportFile(fileName)
            # snpDict.update(currentSnpDict)
            # sampleList += currentSampleList
        # self.__writeLrrBafFileFromDict(snpDict, snpFile, sampleList,
        # outFileName)
        self.__mergeLrrBafFiles(fileToMergeList, outFileName)

    def __doesDirContainAffymetrixData(self, dirName):
        return glob.glob(os.path.join(dirName, '*.cel')) + \
            glob.glob(os.path.join(dirName, '*.cel.gz'))

    def process(self, lrrBafFile, sampleFile, sampleAliasFile, gcFile,
                platform, libDir=None, gw6Dir=None, snpFile=None,
                normalize=True, sampleList=None, targetDir=None):
        if ',' in lrrBafFile:
            lrrBafFile = lrrBafFile.split(',')
        if os.path.isfile(lrrBafFile):
            lrrBafFile = [lrrBafFile]
        if isinstance(lrrBafFile, types.ListType):
            outFileName = os.path.join(os.path.dirname(
                lrrBafFile[0]), 'lrrBaf_%d.txt' % int(normalize))
            Utilities()._runFunc(self._createMergedIlluminaFinalReports, [
                lrrBafFile, snpFile, outFileName, None, sampleList, normalize],
                outFileName)
            lrrBafFile = outFileName
            print 'lrrBafFile = %s' % lrrBafFile
        elif os.path.isdir(lrrBafFile):
            if self.__doesDirContainAffymetrixData(lrrBafFile):
                lrrBafFile = \
                    Utilities.getFunctionResultWithCache(
                        os.path.join(targetDir, 'pennCNV.pyDump'),
                        self._runPennCnvAndGetLrrBafFile, lrrBafFile,
                        libDir, gw6Dir, platform, targetDir)
            else:
                fileList = [fileName.strip() for fileName in os.popen(
                    'find %s -follow -name "*FinalReport.txt"' % lrrBafFile)]
                outFileName = os.path.join(
                    lrrBafFile, 'lrrBaf_%d.txt' % int(normalize))
                Utilities()._runFunc(self._createMergedIlluminaFinalReports, [
                    fileList, snpFile, outFileName, None, sampleList,
                    normalize], outFileName)
                lrrBafFile = outFileName
            print 'lrrBafFile = %s' % lrrBafFile

        rFileName, ascatFile = self.__createRscript(
            sampleFile, lrrBafFile, sampleAliasFile, gcFile, platform)
        rDir = self.__binDir
        if self.__binDir and not os.path.isfile(os.path.join(self.__binDir,
                                                             'R')):
            rDir = None
        self.__checkAndInstallRpackagesIfNecessary(rDir)
        Utilities()._runFunc(R(rDir).runScript, [rFileName], ascatFile)
        return ascatFile


class RunSequenza:

    def __init__(self, binDir=None, rLibDir=None, nbCpus=None, memory=None):
        self.__binDir = binDir
        self.__rLibDir = rLibDir
        if not nbCpus:
            nbCpus = 1
        self.__nbCpus = nbCpus
        self.__binStr = ''
        if binDir and os.path.isfile(os.path.join(binDir, 'python')):
            self.__binStr = binDir + os.path.sep
        self.__memory = memory

    def __getCreateMpileUpFile(self, bamFile, refFile, mpileUpFile,
                               chrName=None):
        optionStr = ''
        if chrName:
            optionStr = '-r %s' % chrName
        cmd = '%s mpileup -f %s -Q 20 %s %s | gzip > %s' % (os.path.join(
            self.__binDir, 'samtools'), refFile, optionStr, bamFile,
            mpileUpFile)
        return cmd

    def _createMpileUpFile(self, bamFile, refFile, mpileUpFile, chrName=None):
        cmd = self.__getCreateMpileUpFile(
            bamFile, refFile, mpileUpFile, chrName)
        Utilities.mySystem(cmd)

    def __getBamDictFromDirAndPattern(self, bamDir, pattern):
        bamDict = {}
        for bamFile in os.popen('find %s -follow -name "*%s"' % (bamDir,
                                                                 pattern)):
            bamFile = bamFile.strip()
            # sampleName, fcName, uplex =
            # IlluminaRun().getSampleNameRunNameAndUplexFromPath(bamFile)
            sampleName = os.path.basename(bamFile).split('.')[0]
            bamDict[sampleName] = bamFile
        return bamDict

    def __appendMpileUpCreation(self, bamFile, refFile, cmdList):
        mpileUpFile = FileNameGetter(bamFile).get('pileup.gz')
        cmdList.append((bamFile, mpileUpFile, Cmd(
            'python %s -P mpileUp -f %s -o %s -r %s' %
            (os.path.abspath(__file__), bamFile, mpileUpFile, refFile))))
        return mpileUpFile

    def __getSequenzaUtils(self):
        for rLibraryDir in R(self.__binDir, libDir=self.__rLibDir).\
                getLibraryPathList():
            fileName = os.path.join(
                rLibraryDir, 'sequenza', 'exec', 'sequenza-utils.py')
            if os.path.isfile(fileName):
                return fileName
        raise NotImplementedError('sequenza does not seem to be installed')

    def _createGcFile(self, refFile):
        cmd = '%spython %s GC-windows -w 50 %s | gzip > %s' % (
            self.__binStr, self.__getSequenzaUtils(), refFile,
            self.__getGcFileFromFasta(refFile))
        Utilities.mySystem(cmd)

    def __getGcFileFromFasta(self, refFile):
        return FileNameGetter(refFile).get('gc50Base.txt.gz')

    def __appendGcFileCreation(self, refFile, cmdList):
        gcFile = self.__getGcFileFromFasta(refFile)
        cmdList.append((refFile, gcFile, Cmd(
            'python %s -P gc -r %s' % (os.path.abspath(__file__), refFile))))
        return gcFile

    def __appendSeqzFileCreation(self, tumorBam, normalBam, refFile, gcFile,
                                 cmdList, targetDir, createPileUp, byChr):
        outFileName = os.path.join(targetDir, '%s_%s.seqz.gz' % (
            os.path.basename(tumorBam).split('.')[0],
            os.path.basename(normalBam).split('.')[0]))
        cmd = 'python %s -P seqz --normalBam %s --tumorBam %s -r %s --gcFile %s\
 --targetDir %s --createMpileUp %d --byChr %d' % (os.path.abspath(__file__),
                                                  normalBam, tumorBam, refFile,
                                                  gcFile, outFileName,
                                                  int(createPileUp),
                                                  int(byChr))
        if self.__binDir:
            cmd += ' -b %s' % self.__binDir
        if self.__nbCpus:
            cmd += ' -n %d' % self.__nbCpus
        memory = 10
        if self.__nbCpus:
            memory *= self.__nbCpus
        cmdList.append((gcFile, outFileName, Cmd(
            cmd, memory=memory, nbCpus=max(1, self.__nbCpus))))
        return outFileName

    def _getIdvdNameSampleNameAndPatientTypeFromLine(self, splittedLine,
                                                     getOrigSampleName=False):
        idvdName, sampleName, seqFile, patientType = splittedLine[:4]
        originalSampleName = sampleName
        sampleName2 = seqFile.split('_')[1]
        if sampleName != sampleName2:
            sampleName = sampleName2
        if getOrigSampleName:
            return idvdName, sampleName, patientType, originalSampleName
        return idvdName, sampleName, patientType

    def _getIdvdToPairDictFromFile(self, fileName):
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        idvdDict = defaultdict(list)
        for splittedLine in fh:
            idvdName, sampleName, patientType = \
                self._getIdvdNameSampleNameAndPatientTypeFromLine(
                    splittedLine)
            if sampleName not in idvdDict[(idvdName, patientType)]:
                idvdDict[(idvdName, patientType)].append(sampleName)
        return idvdDict

    def __convertSegmentFileIntoAscatFormat(self, fileName, outFileName):
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        aIdx = header.index('"A"')
        bIdx = header.index('"B"')
        outFh = CsvFileWriter(outFileName)
        sampleName = os.path.basename(fileName).split('_')[0]
        for splittedLine in fh:
            outFh.write([sampleName, splittedLine[0].strip('"')] +
                        splittedLine[1:3] + [splittedLine[aIdx],
                                             splittedLine[bIdx]])

    def _createAscatFileFromSegmentFiles(self, dirName):
        outFileList = []
        # in glob.glob(os.path.join(dirName, '*', '*_segments.txt')):
        for fileName in os.popen('find %s -follow -name "*_segments.txt"' %
                                 dirName):
            fileName = fileName.strip()
            print 'Converting file "%s" into ASCAT format' % fileName
            outFileName = FileNameGetter(fileName).get('_ascat.txt')
            Utilities()._runFunc(self.__convertSegmentFileIntoAscatFormat,
                                 [fileName, outFileName], outFileName)
            outFileList.append(outFileName)
        outFileName = os.path.join(dirName, 'ascat.txt')
        outFh = CsvFileWriter(outFileName)
        outFh.write(['sample', 'chr', 'startpos',
                     'endpos', 'nMajor', 'nMinor'])
        outFh.close()
        cmd = "cat %s >> %s" % (' '.join(outFileList), outFileName)
        Utilities.mySystem(cmd)
        return outFileName

    def __appendAscatFileCreation(self, dirName, cmdList):
        cmd = 'python %s -P createAscatFile -d %s' % (
            os.path.abspath(__file__), dirName)
        cmdList.append((dirName, os.path.join(dirName, 'ascat'), cmd))

    def __installSequenzaIfNecessary(self):
        r = R(self.__binDir, libDir=self.__rLibDir)
        if r.isPackageInstalled('sequenza'):
            return
        if not r.isPackageInstalled('squash'):
            try:
                r.installPackage('squash')
            except:
                r.installPackageFromUrl(
                    'https://cran.r-project.org/web/packages/squash/')
        if not r.isPackageInstalled('copynumber'):
            r._execString(
                "source('http://bioconductor.org/biocLite.R'); \
biocLite('copynumber')")
        try:
            r.installPackage('sequenza')
        except:
            r.installPackageFromUrl(
                'https://cran.r-project.org/web/packages/sequenza/index.html')

    def _runSequenza(self, seqzFile, chrName=None):
        rStr = '''library(sequenza)

seqz.data <- read.seqz("%(seqzFile)s", chr.name = "%(chrName)s")
gc.stats <- gc.norm(x = seqz.data$depth.r+atio, gc = seqz.data$GC.percent)
gc.vect  <- setNames(gc.stats$raw.mean, gc.stats$gc.values)
seqz.data$adjusted.ratio <- seqz.data$depth.ratio / gc.vect[as.character(
seqz.data$GC.percent)]

png("%(imgFile)s", width=4000, height=1800, res=300)
par(mfrow = c(1,2), cex = 1, las = 1, bty ='l')
matplot(gc.stats$gc.values, gc.stats$raw, type ='b', col = 1, pch =
c(1, 19, 1), lty = c(2, 1, 2), xlab ='GC content (%%)',
ylab ='Uncorrected depth ratio')
legend('topright', legend = colnames(gc.stats$raw), pch = c(1, 19, 1))
hist2(seqz.data$depth.ratio, seqz.data$adjusted.ratio, breaks = prettyLog,
key = vkey, panel.first = abline(0, 1, lty = 2),
xlab ='Uncorrected depth ratio', ylab = 'GC-adjusted depth ratio')
dev.off()

chromosome.view(mut.tab = test$mutations[[1]], baf.windows = test$BAF[[1]],
                 ratio.windows = test$ratio[[1]], min.N.ratio = 1,
                 segments = test$segments[[1]], main = test$chromosomes[1])
''' % {'chrName': chrName, 'imgFile': FileNameGetter(seqzFile).get(
            '_raw_%s.png' % chrName), 'seqzFile': seqzFile}

        sampleName = os.path.basename(seqzFile).split('.')[0]
        rStr = '''library(sequenza)

test <- sequenza.extract("%(seqzFile)s", chromosome.list = 1:22)
png("%(imgFile)s", width=4000, height=1800, res=300)
chromosome.view(mut.tab = test$mutations[[1]], baf.windows = test$BAF[[1]],
                 ratio.windows = test$ratio[[1]], min.N.ratio = 1,
                 segments = test$segments[[1]], main = test$chromosomes[1])
dev.off()

CP.example <- sequenza.fit(test)
sequenza.results(sequenza.extract = test, cp.table = CP.example,
                  sample.id = "%(sampleName)s", out.dir="%(outputDir)s")

''' % {'chrName': chrName, 'imgFile': FileNameGetter(seqzFile).get('png'),
            'seqzFile': seqzFile, 'sampleName': sampleName,
            'outputDir': os.path.join(os.path.dirname(seqzFile), sampleName)}
        R(self.__binDir, libDir=self.__rLibDir).runCmd(
            rStr, FileNameGetter(seqzFile).get('R'))

    def __appendSequenza(self, seqzFile, byChr, refFile, cmdList):
        # for chrName in self.__getChrList(refFile, byChr):
        memory = 8
        if self.__memory:
            memory = self.__memory
        for chrName in [None]:
            currentOutFileName = seqzFile
            if chrName:
                currentOutFileName = seqzFile.replace(
                    '.seqz.gz', '_%s.seqz.gz' % chrName)
            cmd = 'python %s -P seqzR --fileName [input] --chrName %s' % (
                os.path.abspath(__file__), chrName)
            if self.__binDir:
                cmd += ' -b %s' % self.__binDir
            cmdList.append((currentOutFileName, FileNameGetter(
                currentOutFileName).get('R'), Cmd(cmd, memory=memory)))

    def __createSeqzFile(self, tumorBam, normalBam, refFile, gcFile,
                         outFileName, createPileUp, chrName):
        suffix = ''
        progName = 'bam2seqz'
        optionStr = ' -F %s' % refFile
        if chrName:
            suffix = '_%s' % chrName
            optionStr += ' -C %s' % chrName
        if createPileUp:
            cluster = ThreadManager(2)
            tumorPileUp = FileNameGetter(tumorBam).get('pileUp%s.gz' % suffix)
            normalPileUp = FileNameGetter(
                normalBam).get('pileUp%s.gz' % suffix)
            cluster.submit(Utilities()._runFunc, Utilities.mySystem, [
                           self.__getCreateMpileUpFile(tumorBam, refFile,
                                                       tumorPileUp, chrName)],
                           tumorPileUp)
            cluster.submit(Utilities()._runFunc, Utilities.mySystem, [
                           self.__getCreateMpileUpFile(normalBam, refFile,
                                                       normalPileUp, chrName)],
                           normalPileUp)
            cluster.wait()
            normalBam = normalPileUp
            tumorBam = tumorPileUp
            progName = 'pileup2seqz'
            optionStr = ''
        scriptFile = FileNameGetter(outFileName).get('sh')
        cmd = '%spython %s %s -n %s -t %s -gc %s %s | gzip -c > %s' % (
            self.__binStr, self.__getSequenzaUtils(), progName, normalBam,
            tumorBam, gcFile, optionStr, outFileName)
        Utilities.mySystem(cmd, scriptFile)
        if createPileUp:
            Utilities.mySystem('rm %s %s' % (normalPileUp, tumorPileUp))

    def __getChrList(self, refFile, byChr=False):
        chrList = [None]
        if byChr:
            chrList = list(ParseFastaFile(
                refFile)._getSequenceNameListFromFile(True))
            chrList.sort()
            if chrList[0][:3] == 'chr':
                chrList = ['chr%s' % chrName for chrName in range(1, 23)]
            else:
                chrList = range(1, 23)
        chrList.reverse()
        return chrList

    def __mergeSeqzFilesAndClean(self, outFileName, refFile):
        MergeAnnotationFiles().process(outFileName.replace(
            '.seqz.gz', '_%s.seqz.gz'), outFileName, refFile, True)
        Utilities.mySystem('rm %s' %
                           outFileName.replace('.seqz.gz', '_*.seqz.gz'))

    def _createSeqzFile(self, tumorBam, normalBam, refFile, gcFile,
                        outFileName, createPileUp=False, byChr=False):
        chrList = self.__getChrList(refFile, byChr)
        cluster = ThreadManager(self.__nbCpus)
        for chrName in chrList:
            currentOutFileName = outFileName
            if chrName:
                currentOutFileName = outFileName.replace(
                    '.seqz.gz', '_%s.seqz.gz' % chrName)
            cluster.submit(Utilities()._runFunc, self.__createSeqzFile, [
                           tumorBam, normalBam, refFile, gcFile,
                           currentOutFileName, createPileUp, chrName],
                           currentOutFileName)
        cluster.wait()
        if byChr:
            Utilities()._runFunc(self.__mergeSeqzFilesAndClean,
                                 [outFileName, refFile], outFileName)

    def __getTumorAndNormalSampleListFromFile(self, sampleFile):
        idvdDict = self._getIdvdToPairDictFromFile(sampleFile)
        pairList = []
        for (idvdName, patientType), sampleList in idvdDict.iteritems():
            if patientType == 'N':
                continue
            normalSampleList = idvdDict[(idvdName, 'N')]
            if len(normalSampleList) != 1:
                # print idvdName, sampleList, normalSampleList
                print normalSampleList, idvdName, sampleList, idvdDict
                raise NotImplementedError
            for tumorSample in sampleList:
                pairList.append((tumorSample, normalSampleList[0]))
        return pairList

    def process(self, sampleFile, dirName, pattern, targetDir, refFile,
                createPileUp=False, byChr=False):
        bamDict = Utilities.getFunctionResultWithCache(
            os.path.join(dirName, 'bamDictSequenza.pyDump'),
            self.__getBamDictFromDirAndPattern, dirName, pattern)
        Utilities.mySystem('mkdir -p %s' % targetDir)
        if not os.path.isfile(sampleFile):
            sampleFile = sampleFile.split(',')
        if isinstance(sampleFile, types.ListType):
            sampleList = [tuple(sampleName.split(';'))
                          for sampleName in sampleFile]
        else:
            sampleList = self.__getTumorAndNormalSampleListFromFile(sampleFile)
        cmdList = []
        gcFile = self.__appendGcFileCreation(refFile, cmdList)
        for tumorSample, normalSample in sampleList:
            tumorBam = bamDict[tumorSample]
            normalBam = bamDict[normalSample]
            # self.__appendMpileUpCreation(tumorBam, refFile, cmdList)
            # self.__appendMpileUpCreation(normalBam, refFile, cmdList)
            seqzFile = self.__appendSeqzFileCreation(
                tumorBam, normalBam, refFile, gcFile, cmdList, targetDir,
                createPileUp, byChr)
            self.__appendSequenza(seqzFile, byChr, refFile, cmdList)
        if not ProcessFileFromCluster(binPath=self.__binDir)._runCmdList(
                cmdList, refFile, cluster=guessHpc()):
            cmdList = []
            self.__appendAscatFileCreation(targetDir, cmdList)
            ProcessFileFromCluster(binPath=self.__binDir)._runCmdList(
                cmdList, refFile, cluster=guessHpc())


class aCNViewer:
    _LOH = 'LOH'
    _cnLOH = 'cn-LOH'
    _BOTH = 'both'

    def __init__(self, windowSize, percent, binDir=None, useShape=False,
                 sampleFile=None, sampleAliasFile=None, groupColumnName=None,
                 rLibDir=None, rColorFile=None):
        self.__windowSize = windowSize
        self.__percent = percent
        self.__binDir = binDir
        self.__binStr = ''
        if binDir and os.path.isfile(os.path.join(binDir, 'R')):
            self.__binStr = binDir + '/'
        self.__useShape = useShape
        self.__sampleFile = sampleFile
        self.__sampleAliasFile = sampleAliasFile
        self.__groupColumnName = groupColumnName
        self.__sampleDict = None
        if sampleAliasFile:
            self.__sampleDict = \
                RunAscat()._getSampleDictFromFile(sampleAliasFile)
        self.__sampleToGroupDict = None
        if sampleFile and groupColumnName:
            self.__sampleToGroupDict = \
                RunAscat()._getSampleToGroupDictFromFile(
                    sampleFile, groupColumnName, self.__sampleDict)
        if rLibDir:
            rLibDir = os.path.abspath(rLibDir)
        self.__rLibDir = rLibDir
        self.__setColorDictFromFile(rColorFile)

    def __isStrHtmlHexadecimalColor(self, colorStr):
        if len(colorStr) != 7 or colorStr[0] != '#':
            return
        colorStr = colorStr.upper()
        for character in colorStr[1:]:
            if ord(character) < ord('0') or ord(character) > ord('F'):
                return
        return True

    def __checkColorList(self, colorList, rColorList):
        isError = False
        for rColor in colorList:
            if rColor not in rColorList and not \
               self.__isStrHtmlHexadecimalColor(rColor):
                print('Color "%s" is unknown' % rColor)
                isError = True
        if isError:
            raise NotImplementedError

    def __extractSectionAndColorListFromStr(self, partStr):
        from WebExtractor import WebExtractor
        partList = partStr.strip().split('\n')
        if partList[0][0] != '[' or partList[0].strip()[-1] != ']':
            raise NotImplementedError(
                'Color section should be one of ([cnv], [chr], [group])')
        tag = WebExtractor()._getStrIncludedInTag(partList[0], '[', ']')
        tagToExpectedNbColorDict = {'histogram': 10, 'chr': 22, 'heatmap': 10}
        colorList = [color.strip() for color in partList[1:]]
        nbExpectedColors = tagToExpectedNbColorDict.get(tag)
        if nbExpectedColors:
            if len(colorList) != nbExpectedColors:
                raise NotImplementedError('Section "%s" requires %d colors \
but %d were defined' % (tag, nbExpectedColors, len(colorList)))
        rColorFh = R(self.__binDir)._execString('colors()', True)
        rColorList = WebExtractor()._getStrListIncludedInTag(rColorFh.read(),
                                                             '"', '"')
        self.__checkColorList(colorList, rColorList)
        return tag, colorList

    def __setColorDictFromFile(self, rColorFile):
        self.__rColorDict = {}
        if not rColorFile:
            return
        fh = open(rColorFile)
        content = fh.read()
        fh.close()
        partList = content.split('\n\n')
        for partStr in partList:
            tag, colorList = self.__extractSectionAndColorListFromStr(partStr)
            self.__rColorDict[tag] = colorList

    def processAll(self, ascatFile, chrFile, targetDir, ploidyFile,
                   percentList, baseList, histogram=True, merge=False,
                   dendrogram=False, plotAll=False, centromereFile=None,
                   groupColumnName=None):
        Utilities.mySystem('mkdir -p %s' % targetDir)
        cluster = guessHpc()
        for baseNb in baseList:
            if not baseNb:
                continue
            cmdList = []
            cmd = 'python %s -f %s --ploidyFile %s -w %d -c %s --dendrogram \
%d --histogram %d -t %s -u %d -m %d --plotAll %d'
            if centromereFile:
                cmd += ' --centromereFile %s' % centromereFile
            if groupColumnName:
                cmd += ' -G "%s"' % groupColumnName
            if self.__binDir:
                cmd += ' -b %s' % self.__binDir
            cmdList.append((ascatFile,
                            os.path.join(targetDir,
                                         os.path.basename(ascatFile) +
                                         '_%db' % baseNb),
                            Cmd(cmd % (os.path.abspath(__file__), ascatFile,
                                       ploidyFile, baseNb, chrFile, dendrogram,
                                       histogram, targetDir, self.__useShape,
                                       merge, plotAll), nbCpus=1, memory=40)))
            ProcessFileFromCluster()._runCmdList(cmdList, ascatFile, cluster)
        for percent in percentList:
            cmdList = []
            cmd = 'python %s -f %s --ploidyFile %s -p %f -c %s --dendrogram %d\
 --histogram %d -t %s -u %d -m %d --plotAll %d'
            if centromereFile:
                cmd += ' --centromereFile %s' % centromereFile
            if groupColumnName:
                cmd += ' -G "%s"' % groupColumnName
            if self.__binDir:
                cmd += ' -b %s' % self.__binDir
            cmdList.append((ascatFile,
                            os.path.join(targetDir,
                                         os.path.basename(ascatFile) +
                                         '_%fp' % percent),
                            Cmd(cmd % (os.path.abspath(__file__),
                                       ascatFile, ploidyFile, percent, chrFile,
                                       dendrogram, histogram, targetDir,
                                       self.__useShape, merge, plotAll),
                                nbCpus=1, memory=40)))
            ProcessFileFromCluster()._runCmdList(cmdList, ascatFile, cluster)

    def __getChrSizeDictFromFile(self, fileName):
        chrSizeDict = {}
        for splittedLine in ReadFileAtOnceParser(fileName):
            chrName = splittedLine[0]
            if chrName[:3] == 'chr':
                chrName = chrName[3:]
            chrSizeDict[chrName] = int(splittedLine[1])
        return chrSizeDict

    def __getSampleListFromFile(self, fileName):
        sampleList = []
        fh = ReadFileAtOnceParser(fileName)
        fh.getSplittedLine()
        for splittedLine in fh:
            sampleList.append(splittedLine[0])
        return sampleList

    def __getNextSampleLineDictAndSampleName(self, fh):
        lineDict = SimpleMultiDict()
        sampleName = None
        while fh.hasLinesLeft():
            splittedLine = fh.getSplittedLine()
            currentSampleName = splittedLine[0]
            if not sampleName:
                sampleName = currentSampleName
            if currentSampleName != sampleName:
                fh.restore(splittedLine)
                break
            lineDict[splittedLine[1]] = splittedLine[2:]
        return lineDict, sampleName

    def _getLineListInBetween(self, lineList, start, endSegment):
        fragPos = Position(1, start, endSegment)
        selectedLineList = []
        while lineList:
            splittedLine = lineList.pop(0)
            currentStart = int(splittedLine[0])
            currentEnd = int(splittedLine[1])
            currentPos = Position(1, currentStart, currentEnd)
            overlapPos = fragPos.getOverlapPosition(currentPos)
            if overlapPos:
                selectedLineList.append(
                    [overlapPos.start, overlapPos.end] + splittedLine[2:])
                if currentEnd > fragPos.end:
                    lineList.insert(
                        0, [fragPos.end + 1, currentEnd] + splittedLine[2:])
                    break
            elif currentStart > fragPos.end:
                lineList.insert(0, splittedLine)
                break
        return selectedLineList

    def __getCurrentPloidyFromLineList(self, lineList):
        totalSize = totalPloidy = 0.
        for splittedLine in lineList:
            start = int(splittedLine[2])
            end = int(splittedLine[3])
            fragSize = end - start + 1
            totalSize += fragSize
            ploidy = int(splittedLine[4]) + int(splittedLine[5])
            totalPloidy += ploidy * fragSize
        return int(round(totalPloidy / totalSize))

    def __getCurrentPloidyFromLineList2(self, lineList):
        fragDict = defaultdict(int)
        for splittedLine in lineList:
            start = int(splittedLine[2])
            end = int(splittedLine[3])
            fragSize = end - start + 1
            if 'NA' in splittedLine[4:6]:
                continue
            ploidy = int(splittedLine[4]) + int(splittedLine[5])
            fragDict[ploidy] += fragSize
            # valueList.append(ploidy)
        # valueList.sort()
        # return getMedianFromList(valueList)
        fragSizeToPloidyDict = SimpleMultiDict(
            [[fragSize, ploidy] for ploidy, fragSize in fragDict.iteritems()])
        maxFragSize = max(fragSizeToPloidyDict)
        ploidyList = fragSizeToPloidyDict.getall(maxFragSize)
        if len(ploidyList) > 1:
            print 'WARNING: several fragments with same size: %d -> %s' % \
                  (maxFragSize, str(ploidyList))
        return ploidyList[0]

    def __getNewSegment(self, prevSegment, currentSegment, defaultPloidy):
        prevEnd = int(prevSegment[1])
        currentStart = int(currentSegment[0])
        newStart = prevEnd + 1
        newEnd = currentStart - 1
        if newEnd >= newStart:
            return [newStart, newEnd, defaultPloidy, 0]

    def _getGappedFilledSegmentListFromList(self, sampleName, chrName,
                                            segmentList, startSegment,
                                            endSegment, defaultPloidy=2):
        # print '+' * 100
        start = int(segmentList[0][0])
        end = int(segmentList[-1][1])
        newSegmentList = []
        if startSegment < start:
            newSegmentList.append(
                [sampleName, chrName, startSegment, start - 1, defaultPloidy,
                 0])
        segment = segmentList.pop(0)
        newSegmentList.append([sampleName, chrName] + segment)
        for currentSegment in segmentList:
            newSegment = self.__getNewSegment(
                segment, currentSegment, defaultPloidy)
            # print '@', currentSegment, newSegment
            if newSegment:
                newSegmentList.append([sampleName, chrName] + newSegment)
            newSegmentList.append([sampleName, chrName] + currentSegment)
            segment = currentSegment
        if end < endSegment:
            newSegmentList.append(
                [sampleName, chrName, end + 1, endSegment, defaultPloidy, 0])
        return newSegmentList

    def _getSegmentListFromList(self, lineList, sampleName, chrName,
                                chrSizeDict, defaultPloidy=2):
        middleSegmentList = []
        start = int(lineList[0][0])
        chrEnd = chrSizeDict[str(chrName)]
        end = min(int(lineList[-1][1]), chrEnd)
        startSegmentList = self._getSegmentStartList(
            start, sampleName, chrName, defaultPloidy)

        endSegmentList = self._getEndSegmentList(
            end, sampleName, chrName, chrEnd, defaultPloidy)
        startSegmentNb = self.__getNbSegmentsFromPos(start)
        startSegment = startSegmentNb * self.__windowSize + 1
        endSegment = startSegment + self.__windowSize - 1
        while lineList:
            totalSize = 0
            currentLineList = self._getLineListInBetween(
                lineList, startSegment, endSegment)
            # print 'C = %s, S = %d, E = %d, L = %d' % (chrName, startSegment,
            # endSegment, len(currentLineList))
            # if currentLineList:
            # print currentLineList
            if currentLineList:
                currentLineList = self._getGappedFilledSegmentListFromList(
                    sampleName, chrName, currentLineList, startSegment,
                    endSegment, defaultPloidy)
                middleSegmentList.append(
                    [sampleName, chrName, startSegment, endSegment,
                     self.__getCurrentPloidyFromLineList2(currentLineList)])
            else:
                middleSegmentList.append(
                    [sampleName, chrName, startSegment, endSegment,
                     defaultPloidy])
            startSegment += self.__windowSize
            endSegment = min(endSegment + self.__windowSize, chrEnd)
            if startSegment > endSegment:
                break
        return startSegmentList + middleSegmentList + endSegmentList

    def __getNbSegmentsFromPos(self, pos):
        residue = pos % self.__windowSize
        nbSegments = (pos - residue) / self.__windowSize
        if not residue:
            nbSegments -= 1
        return nbSegments

    def _getSegmentStartList(self, start, sampleName, chrName,
                             defaultPloidy=2):
        nbSegments = self.__getNbSegmentsFromPos(start)
        return [[sampleName, chrName, 1 + i * self.__windowSize,
                 self.__windowSize * (i + 1), defaultPloidy] for i in
                range(nbSegments)]

    def _getEndSegmentList(self, end, sampleName, chrName, chrEnd,
                           defaultPloidy=2):
        nbSegments = self.__getNbSegmentsFromPos(end)
        endSegmentNb = self.__getNbSegmentsFromPos(chrEnd)
        segmentList = [[sampleName, chrName, 1 + i * self.__windowSize,
                        self.__windowSize * (i + 1), defaultPloidy] for i in
                       range(nbSegments + 1, endSegmentNb)]
        lastSegmentStart = endSegmentNb * self.__windowSize + 1
        if lastSegmentStart > end:
            segmentList.append(
                [sampleName, chrName, lastSegmentStart, chrEnd, defaultPloidy])
        return segmentList

    def __mergeLastTwoSegmentsIfNecessary(self, segmentList, i=-1,
                                          reverse=False, mergeAnyway=False,
                                          isLOH=None):
        if len(segmentList) < 2:
            return
        sampleName1, chrName1, lastSegmentStart1, chrEnd1, defaultPloidy1 = \
            segmentList[i]
        sampleName0, chrName0, lastSegmentStart0, chrEnd0, defaultPloidy0 = \
            segmentList[i - 1]
        if sampleName1 != sampleName0:
            raise NotImplementedError('Expecting same samples but found %s \
and %s:%s\n%s' % (sampleName0, sampleName1, segmentList[i - 1],
                  segmentList[i]))
        if chrName0 != chrName1:
            raise NotImplementedError('Expecting same chr but found %s and %s:\
 %s\n%s' % (chrName0, chrName1, segmentList[i - 1], segmentList[i]))
        segmentSize1 = chrEnd1 - lastSegmentStart1 + 1
        segmentSize0 = chrEnd0 - lastSegmentStart0 + 1
        if not mergeAnyway and ((not reverse and segmentSize1 >=
                                 self.__windowSize * 1. / 2) or
                                (reverse and segmentSize0 >=
                                 self.__windowSize * 1. / 2)):
            # print 'No merge', sampleName0, chrName0, lastSegmentStart0,
            # chrEnd0, lastSegmentStart1, chrEnd1
            return
        segmentList.pop(i - 1)
        # print defaultPloidy0, segmentSize0, defaultPloidy1, segmentSize1
        if isLOH:
            ploidyDict = SimpleMultiDict()
            ploidyDict[segmentSize0] = defaultPloidy0
            ploidyDict[segmentSize1] = defaultPloidy1
            maxSize = max(ploidyDict)
            ploidyList = ploidyDict.getall(maxSize)
            if len(ploidyList) == 1:
                newPloidy = ploidyList[0]
            else:
                ploidyList = [ploidy for ploidy in ploidyList if not ploidy]
                if len(ploidyList) == 1:
                    newPloidy = ploidyList[0]
                else:
                    print segmentList
                    print ploidyDict
                    raise NotImplementedError
        else:
            newPloidy = int(round((1. * defaultPloidy0 * segmentSize0 +
                                   defaultPloidy1 * segmentSize1) /
                                  (segmentSize0 + segmentSize1)))
        lastIndex = i - 1
        if i == -1:
            lastIndex = i
        segmentList[lastIndex] = [sampleName0, chrName0,
                                  lastSegmentStart0, chrEnd1, newPloidy]
        # print 'There', sampleName0, chrName0, lastSegmentStart0, chrEnd0,
        # lastSegmentStart1, chrEnd1, defaultPloidy0, defaultPloidy1, newPloidy

    def __getNewLineStartAndEnd(self, linePos, overlapPos):
        if linePos.start < overlapPos.start:
            start = linePos.start
            end = overlapPos.start - 1
        else:
            start = overlapPos.end + 1
            end = linePos.end
        if start > end:
            print linePos, overlapPos, start, end
            raise NotImplementedError
        return start, end

    def _removeEventOverCentromereFromList(self, lineList, centroStart,
                                           centroEnd):
        # if (self.__sampleName, self.__chrName) == ('GSM248805', 9):
            # print '~' * 55
            # print '_removeEventOverCentromereFromList'
        chrPos = Position(None, centroStart, centroEnd)
        toRemoveList = []
        shift = 0
        if lineList and not ValueParser().isNb(lineList[0][0]):
            shift = 2
        # print '@' * 100
        toReplaceList = []
        for line in lineList:
            lineStart, lineEnd = line[shift:shift + 2]
            linePos = Position(None, lineStart, lineEnd)
            overlapPos = linePos.getOverlapPosition(chrPos)
            # print 'linePos', linePos
            # print 'overrl', overlapPos
            if overlapPos:
                if overlapPos == linePos:
                    # print '=='
                    toRemoveList.append(line)
                else:
                    start, end = self.__getNewLineStartAndEnd(
                        linePos, overlapPos)
                    # print 'not eql', start, end
                    endLine = None
                    if int(line[shift + 1]) > centroEnd and \
                       centroStart > int(line[shift + 0]):
                        endLine = [centroEnd + 1,
                                   line[shift + 1]] + line[shift + 2:]
                        if shift:
                            endLine = line[:2] + endLine
                        # print 'ENDline', endLine, line[1], end
                    line[shift + 0] = start
                    line[shift + 1] = end
                    if endLine:
                        toReplaceList.append((line, endLine))
        # if (self.__sampleName, self.__chrName) == ('GSM248805', 9):
            # print '%d toRemove, %d toReplace' % (len(toRemoveList),
            # len(toReplaceList))
            # print 'toReplace:'
        for line, endLine in toReplaceList:
            # if (self.__sampleName, self.__chrName) == ('GSM248805', 9):
                # print line, endLine
            idx = lineList.index(line)
            lineList.insert(idx + 1, endLine)
        # if (self.__sampleName, self.__chrName) == ('GSM248805', 9):
            # print '@' * 20
            # print 'toRemove:'
        for line in toRemoveList:
            # if (self.__sampleName, self.__chrName) == ('GSM248805', 9):
                # print line
            lineList.remove(line)

    def _mergeCentromericSegmentsIfNecessary(self, segmentList,
                                             mergeCentromereSegments=None,
                                             nbExpectedSegments=None,
                                             isLOH=None):
        prevSegment = None
        for i, segment in enumerate(segmentList):
            if prevSegment and not mergeCentromereSegments and \
               prevSegment[3] != segment[2] - 1:
                # print 'Merge', i
                nbSegments = len(segmentList)
                self.__mergeLastTwoSegmentsIfNecessary(
                    segmentList, i - 1, isLOH=isLOH)
                # print 1, segmentList
                shift = nbSegments - len(segmentList)
                if shift:
                    idx = i
                else:
                    idx = i + 1
                self.__mergeLastTwoSegmentsIfNecessary(
                    segmentList, idx, True, isLOH=isLOH)
                # print 2, segmentList
                break
            elif prevSegment and mergeCentromereSegments and \
                    prevSegment[3] != segment[2] - 1:
                nbSegments = len(segmentList)
                if nbSegments == nbExpectedSegments:
                    continue
                # print '~' * 50
                # print prevSegment
                # print segment
                # print ']' * 30
                self.__mergeLastTwoSegmentsIfNecessary(
                    segmentList, i, isLOH=isLOH)
                # print 1, segmentList
                # shift = nbSegments - len(segmentList)
                # if shift:
                # idx = i+1
                # else:
                # idx = i+2
                # self.__mergeLastTwoSegmentsIfNecessary(segmentList, idx,
                # True)
                break
            prevSegment = segment

    def _fillCentromericGapsIfNecessary(self, segmentList, defaultPloidy):
        prevSegment = None
        for i, segment in enumerate(segmentList):
            if prevSegment and prevSegment[3] != segment[2] - 1:
                # print 'Merge', i
                segmentList.insert(
                    i, segment[:2] + [prevSegment[3] + 1, segment[2] - 1,
                                      defaultPloidy])
                break
            prevSegment = segment

    def __getCentromericSegmentFromCentromerePosition(self, centroPos):
        start = 1
        while True:
            pos = Position(centroPos.ctgId, start,
                           start + self.__windowSize - 1)
            if pos.getOverlapPosition(centroPos):
                return pos
            start = pos.end + 1

    def __mergeCentromericSegmentsIntoOne(self, segmentList, chrName,
                                          centroStart, centroEnd):
        # print '__mergeCentromericSegmentsIntoOne ' + '@' * 100
        centroSegmentDict = SimpleMultiDict()
        centroPos = Position(str(chrName), centroStart, centroEnd)
        centroSegment = self.__getCentromericSegmentFromCentromerePosition(
            centroPos)
        # print centroPos, centroSegment, len(segmentList)
        for i, (sampleName, chrName, startSegment, endSegment, ploidy) in \
                enumerate(segmentList):
            pos = Position(str(chrName), startSegment, endSegment)
            # print 'pos', pos
            if pos.getOverlapPosition(centroSegment):
                # print 'POS', pos
                centroSegmentDict[sampleName] = i
        for sampleName in centroSegmentDict:
            currentSegmentIdxList = centroSegmentDict.getall(sampleName)
            if len(currentSegmentIdxList) > 2:
                raise NotImplementedError('#segments > 2 for sample = %s\n\
Segments = %s' % (sampleName, currentSegmentIdxList))
            idx = currentSegmentIdxList[0]
            for i in currentSegmentIdxList[1:]:
                if i - idx != 1:
                    raise NotImplementedError('Indexes are not consecutive \
for sample %s, idx = %s' % (sampleName, currentSegmentIdxList))
                idx = i
            if len(currentSegmentIdxList) == 2:
                # print '@' * 100
                # print 'Sample %s: merging segmentList %s' % (sampleName,
                # [segmentList[i] for i in currentSegmentIdxList])
                maxIdx = max(currentSegmentIdxList)
                self.__mergeLastTwoSegmentsIfNecessary(segmentList, maxIdx)
                # print segmentList[maxIdx-1:maxIdx+1]
                # print '=' * 50

    def __getChrSegmentListFromPosition(self, start, end, chrName):
        endSegmentNb = int(math.ceil(1. * end / self.__windowSize))
        startSegmentNb = int(1. * start / self.__windowSize)
        posList = []
        if endSegmentNb == startSegmentNb:
            endSegmentNb += 1
        for i in range(startSegmentNb, endSegmentNb):
            posList.append(
                Position(chrName, i * self.__windowSize + 1,
                         (i + 1) * self.__windowSize))
        return posList

    def __cutSegmentsAccordingToStep(self, segmentList, chrName):
        newSegmentList = []
        chrName = str(chrName)
        segmentDict = SimpleMultiDict()
        for segment in segmentList:
            segChrName = str(segment[1])
            if segChrName != chrName:
                raise NotImplementedError(
                    'Inconsistent chrs: %s != %s' % (segChrName, chrName))
            chrSegmentList = self.__getChrSegmentListFromPosition(
                segment[2], segment[3], chrName)
            pos = Position(segChrName, *segment[2:4])
            # print 'Current seg', segment, self.__windowSize
            # print 'chr pos:'
            # for chrPos in chrSegmentList:
            # print chrPos
            # print '@' * 10
            for chrPos in chrSegmentList:
                overlapPos = pos.getOverlapPosition(chrPos)
                currentSegment = copy.copy(segment)
                # print chrName, chrPos, segment, overlapPos
                currentSegment[2] = overlapPos.start
                currentSegment[3] = overlapPos.end
                # newSegmentList.append(currentSegment)
                segmentDict[(chrPos.start, chrPos.end)] = currentSegment
        for chrStart, chrEnd in Utilities.getOrderedKeys(segmentDict):
            # print '@' * 50
            # print chrStart, chrEnd
            currentSegmentList = segmentDict.getall((chrStart, chrEnd))
            # print currentSegmentList
            if len(currentSegmentList) > 1:
                self.__mergeLastTwoSegmentsIfNecessary(
                    currentSegmentList, mergeAnyway=True)
            if len(currentSegmentList) != 1:
                print currentSegmentList
                raise NotImplementedError
            currentSegment = currentSegmentList[0]
            currentSegment[2] = chrStart
            currentSegment[3] = chrEnd
            newSegmentList.append(currentSegment)
        segmentList[:] = newSegmentList

    def __createMatrixFileAndGetDicts(self, ascatFile, chrFile, targetDir,
                                      ploidyFile, centromereFile=None,
                                      mergeCentromereSegments=None):
        centromereDict = None
        if centromereFile:
            centromereDict = self.__getCentromereDictFromFile(centromereFile)
            print 'CENTRO:', centromereDict
            dumpFileName = FileNameGetter(ascatFile).get('_%s.pyDump' % os.path.basename(
                    centromereFile).split('.')[0])
            if targetDir:
                dumpFileName = os.path.join(targetDir, os.path.basename(dumpFileName))
            ascatFile = Utilities.getFunctionResultWithCache(
                dumpFileName,
                self.__createFileWithTruncatedCentromereData, ascatFile,
                centromereFile, centromereDict, targetDir)
        if self.__windowSize:
            self.__suffix = str(self.__windowSize) + 'nt'
        elif self.__percent:
            self.__suffix = str(self.__percent) + 'pc'
        ploidyDict = DefaultPloidyDict()
        if ploidyFile:
            ploidyDict = self._getPloidyDictFromFile(ploidyFile)
        print ascatFile, len(ploidyDict), ploidyDict, ploidyFile
        Utilities.mySystem('mkdir -p %s' % targetDir)
        chrSizeDict = self.__getChrSizeDictFromFile(chrFile)
        # sampleList = self.__getSampleListFromFile(ascatFile)
        # print str(len(sampleList))+" samples found : "+", ".join(sampleList)
        dumpFileName = ascatFile + '_%s_%d.pyDump5' % (self.__suffix,
                                                       mergeCentromereSegments)
        if targetDir:
            dumpFileName = os.path.join(targetDir, os.path.basename(dumpFileName))
        outFileName, matrixDict = Utilities.getFunctionResultWithCache(
            dumpFileName,
            self.__createSegmentPloidyFile, ascatFile, chrSizeDict, targetDir,
            ploidyDict, centromereDict, mergeCentromereSegments)

        return outFileName, matrixDict, ploidyDict, chrSizeDict, centromereDict

    def __calculatePloidyFromLine(self, splittedLine):
        ploidyDict = defaultdict(int)
        for ploidy in splittedLine[1:]:
            ploidyDict[int(ploidy)] += 1
        maxNb = max(ploidyDict.values())
        # print ploidyDict, maxNb
        maxPloidyList = []
        for ploidy, nb in ploidyDict.iteritems():
            if nb == maxNb:
                maxPloidyList.append(ploidy)
        if len(maxPloidyList) > 1:
            maxPloidyList.sort()
            # raise NotImplementedError('Could not deduce ploidy for sample %s 
            # as there are as many segments with ploidies %s' %(splittedLine[0]
            # maxPloidyList))
        return maxPloidyList[0]

    def _createPloidyFile2(self, ascatFile, ploidyFile=None):
        fh = ReadFileAtOnceParser(ascatFile)
        header = fh.getSplittedLine()
        outFh = CsvFileWriter(FileNameGetter(ascatFile).get('_ploidy2.txt'))
        outFh.write(['Sample', 'Ploidy'])
        ploidyDict = defaultdict(int)
        baseDict = defaultdict(int)
        for splittedLine in fh:
            sampleName, chrName, start, end, nMajor, nMinor = splittedLine
            nbBases = int(end) - int(start) + 1
            baseDict[sampleName] += nbBases
            ploidyDict[sampleName] += (int(nMajor) + int(nMajor)) * nbBases
        if ploidyFile:
            otherPloidyDict = self._getPloidyDictFromFile(ploidyFile)
        nbErrors = 0
        for sampleName in Utilities.getOrderedKeys(ploidyDict):
            ploidy = ploidyDict[sampleName]
            nbBases = baseDict[sampleName]
            ploidy = int(1. * ploidy / nbBases)
            if ploidyFile and sampleName in otherPloidyDict:
                otherPloidy = otherPloidyDict[sampleName]
                if ploidy != otherPloidy:
                    print 'Different ploidies for sample %s: %d != %d' % \
                          (sampleName, ploidy, otherPloidy)
                    print nbBases, ploidyDict[sampleName]
                    nbErrors += 1
            outFh.write([sampleName, ploidy])
        if ploidyFile:
            print 'nbErrors = %d' % nbErrors

    def _createPloidyFile(self, ascatFile, chrFile, targetDir, ploidyFile,
                          centromereFile=None, mergeCentromereSegments=None,
                          otherPloidyFile=None):
        outFileName, matrixDict, ploidyDict, chrSizeDict, centromereDict = \
            self.__createMatrixFileAndGetDicts(
                ascatFile, chrFile, targetDir, ploidyFile,
                centromereFile, mergeCentromereSegments)
        matrixFile = self.__createMatrixFile(
            matrixDict, targetDir, os.path.basename(ascatFile).split('.')[0],
            ploidyFile)
        print 'Matrix file: ', matrixFile
        fh = ReadFileAtOnceParser(matrixFile)
        targetFileName = FileNameGetter(outFileName).get('_ploidy.txt')
        outFh = CsvFileWriter(targetFileName)
        header = fh.getSplittedLine()
        nbErrors = 0
        header += ['ploidy']
        if self.__sampleFile and self.__sampleToGroupDict:
            header += ['group']
        outFh.write(header)
        if otherPloidyFile:
            otherPloidyDict = self._getPloidyDictFromFile(otherPloidyFile)
        for splittedLine in fh:
            try:
                ploidy = self.__calculatePloidyFromLine(splittedLine)
            except:
                print 'otherPloidyFile = ', otherPloidyFile, ploidyFile
                print len(splittedLine), splittedLine
                countDict = defaultdict(int)
                for p in splittedLine:
                    countDict[p] += 1
                print countDict
                raise
            sampleName = splittedLine[0]
            if otherPloidyFile and sampleName in otherPloidyDict:
                otherPloidy = otherPloidyDict[sampleName]
                if ploidy != otherPloidy:
                    print 'Different ploidies for sample %s: %d != %d' % \
                          (sampleName, ploidy, otherPloidy)
                    nbErrors += 1
                # else:
                    # print 'Ok for sample %s' % splittedLine[0]
            splittedLine += [ploidy]
            if self.__sampleFile and self.__sampleToGroupDict:
                splittedLine += [self.__sampleToGroupDict.get(sampleName, '')]
            outFh.write(splittedLine)
        if otherPloidyFile:
            print 'nbErrors = %d' % nbErrors
        return targetFileName

    def __updateSegmentLengths(self, segmentList, chrName):
        segmentList[:] = self.__cutSegmentsAccordingToStep(
            segmentList, chrName)
        # segmentDict =

    def _getSegmentLineListFromLineDict(self, lineDict, sampleName,
                                        chrSizeDict, defaultPloidy=2,
                                        centromereDict=None, isLoh=False,
                                        mergeCentromereSegments=False):
        segmentList = []
        nbExpectedSegments = None
        if self.__percent:
            nbExpectedSegments = int(100 / self.__percent)
        for chrName in range(1, 23):
            chrLineList = lineDict.getall(str(chrName))
            if self.__percent:
                self.__windowSize = int(
                    math.ceil(chrSizeDict[str(chrName)] *
                              self.__percent / 100.))
                print 'Using %d window size for chr %s' % (self.__windowSize,
                                                           chrName)
            # print 'Chr %s' % chrName
            # print len(chrLineList)
            if not chrLineList:
                chrLineList = [
                    [1, chrSizeDict[str(chrName)], defaultPloidy, 0]]
            # if isLoh and centromereDict:
                # if 'P51T' in sampleName:
                # print '@' * 100
                # print chrLineList
                # self._removeEventOverCentromereFromList(chrLineList,
                # *centromereDict[str(chrName)])
                # if 'P51T' in sampleName:
                # print ':' * 50
                # print chrLineList

            currentSegmentList = self._getSegmentListFromList(
                chrLineList, sampleName, chrName, chrSizeDict, defaultPloidy)
            if chrName == 21:
                print '@' * 50
                print currentSegmentList
                print '#' * 40
            # if sampleName == 'GSM248805' and chrName == 9:
                # print isLoh, ':' * 50
                # print centromereDict
                # print chrLineList
                # print currentSegmentList
            if not isLoh and centromereDict:
                # if 'P51T' in sampleName:
                    # print '>' * 50
                    # print currentSegmentList
                self.__sampleName = sampleName
                self.__chrName = chrName
                self._removeEventOverCentromereFromList(
                    currentSegmentList, *centromereDict[str(chrName)])
                # if sampleName == 'GSM248805' and chrName == 9:
                # print ':' * 50
                # print currentSegmentList
            # if 'P51T' in sampleName:
                # print '=' * 50
                # print currentSegmentList
                if mergeCentromereSegments:
                    # print 'MERGING'
                    # print currentSegmentList
                    self.__cutSegmentsAccordingToStep(
                        currentSegmentList, chrName)
                    # print currentSegmentList
                    # print '~' * 20
                else:
                    self._mergeCentromericSegmentsIfNecessary(
                        currentSegmentList)
            # if not isLoh and centromereDict:
                # self._fillCentromericGapsIfNecessary(currentSegmentList,
                # defaultPloidy)
            # if 'P51T' in sampleName:
                # print '>' * 50
                # print currentSegmentList
            if isLoh and centromereDict:
                self._removeEventOverCentromereFromList(
                    currentSegmentList, *centromereDict[str(chrName)])
                self._mergeCentromericSegmentsIfNecessary(
                    currentSegmentList, isLOH=isLoh)
                # self._fillCentromericGapsIfNecessary(currentSegmentList, 0)

            # if 'P51T' in sampleName:
                # print '<' * 50
                # print currentSegmentList
            self.__mergeLastTwoSegmentsIfNecessary(currentSegmentList)
            # if sampleName == 'GSM248805' and chrName == 3:
            # print '<' * 50
            # print currentSegmentList
            segmentList += currentSegmentList
            # if mergeCentromereSegments:
            # self.__mergeCentromericSegmentsIntoOne(segmentList, chrName,
            # *centromereDict[str(chrName)])
        return segmentList

    def __createSegmentPloidyFile(self, ascatFile, chrSizeDict, targetDir,
                                  ploidyDict, centromereDict=None,
                                  mergeCentromereSegments=False):
        fh = ReadFileAtOnceParser(ascatFile)
        header = fh.getSplittedLine()
        outFileName = os.path.join(targetDir,
                                   os.path.basename(ascatFile).split('.')[
                                       0] + '_segments_%s.txt' % self.__suffix)
        outFh = CsvFileWriter(outFileName)
        matrixDict = {}
        isLoh = sum(ploidyDict.values()) == 0
        while fh.hasLinesLeft():
            lineDict, sampleName = self.__getNextSampleLineDictAndSampleName(
                fh)
            defaultPloidy = ploidyDict.get(sampleName.split('.')[0], 2)
            if sampleName not in ploidyDict:
                print 'Passing sample "%s"' % sampleName
                continue
            print 'Processing sample %s with ploidy %d' % (sampleName,
                                                           defaultPloidy)
            segmentList = self._getSegmentLineListFromLineDict(
                lineDict, sampleName, chrSizeDict, defaultPloidy,
                centromereDict, isLoh, mergeCentromereSegments)

            outFh.writeAllLinesAtOnce(segmentList)
            matrixDict[sampleName.split('.')[0]] = \
                      [['%s:%d-%d' % (chrName, start, end), alleleNb]
                       for currentSampleName, chrName, start, end, alleleNb in
                       segmentList]
        return outFileName, matrixDict

    def __getSegmentListFromFile(self, fileName):
        fh = ReadFileAtOnceParser(fileName)
        segmentList = []
        for splittedLine in fh:
            segmentList.append((splittedLine[0], splittedLine[-2:]))
        return segmentList

    def _compareSegmentFiles(self, fileName, fileName2):
        fh1 = ReadFileAtOnceParser(fileName)
        fh2 = ReadFileAtOnceParser(fileName2)
        for splittedLine1 in fh1:
            splittedLine2 = fh2.getSplittedLine()
            if splittedLine1[-2:] != splittedLine2[-2:]:
                if abs(int(splittedLine1[-1]) - int(splittedLine2[-1])) > 1:
                    print 'difference', splittedLine1[:2], splittedLine2[:2],\
                          splittedLine1[-2:], splittedLine2[-2:]

    def _getPloidyDictFromFile(self, fileName):
        ploidyDict = {}
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        ploidyIdx = len(header) - 1
        if 'ploidy' in header:
            ploidyIdx = header.index('ploidy')
        header = [columnName.lower() for columnName in header]
        sampleIdx = header.index('sample')
        if not header[ploidyIdx].strip():
            ploidyIdx -= 1
        # print len(header), header
        for splittedLine in fh:
            # print splittedLine
            ploidyDict[splittedLine[sampleIdx].split(
                '_')[0]] = int(splittedLine[ploidyIdx])
        return ploidyDict

    def __getPloidyKeyFromPloidy(self, currentPloidy):
        return max(-4, currentPloidy) if currentPloidy < 0 else \
            min(6, currentPloidy)

    def __getLohDataDictFromList(self, dataList):
        dataDict = {}
        for sampleName, in dataList:
            return
        return dataDict

    def __getPosFromKey(self, key):
        chrName, key = key.split(':')
        start, end = key.split('-')
        return Position(chrName, start, end)

    def __getKeyListOverlappingPos(self, currentLohDataDict, chrPos):
        keyList = []
        for key in currentLohDataDict:
            pos = self.__getPosFromKey(key)
            if chrPos.getOverlapPosition(pos):
                keyList.append(key)
        return keyList

    def __getLohNbDictAndKeyListFromChrPos(self, lohDataDict, chrPos):
        lohNbDict = defaultdict(int)
        chrPos = self.__getPosFromKey(chrPos)
        keySet = set()
        for sampleName, currentLohDataDict in lohDataDict.iteritems():
            currentPloidy = currentLohDataDict.get(chrPos)
            for key in self.__getKeyListOverlappingPos(currentLohDataDict,
                                                       chrPos):
                keySet.add(key)
                currentPloidy = currentLohDataDict.get(chrPos)
                if currentPloidy:
                    lohNbDict[key] += 1
        keyList = []
        for key in keySet:
            pos = self.__getPosFromKey(key)
            keyList.append(((pos.ctgId, pos.start, pos.end), key))
        keyList.sort()
        return lohNbDict, [key for pos, key in keyList]

    def __getSampleLohListFromDictAndKey(self, lohDataDict, chrPos):
        # nbLoh = 0
        sampleList = []
        for sampleName, currentLohDataDict in lohDataDict.iteritems():
            currentPloidy = currentLohDataDict.get(chrPos)
            if currentPloidy:
                # nbLoh += 1
                sampleList.append(sampleName)
        # return nbLoh
        return sampleList

    def __writeLOH(self, lohDataDict, outFh2, nbVal, chrPos, chrName, start,
                   end, sampleLohFh):
        nbLoh = self.__getSampleLohListFromDictAndKey(lohDataDict, chrPos)
        if not nbLoh:
            lohNbList = []
            lohNbDict, keyList = self.__getLohNbDictAndKeyListFromChrPos(
                lohDataDict, chrPos)
            for key in keyList:
                lohSampleList = self.__getSampleLohListFromDictAndKey(
                    lohDataDict, key)
                lohNbList.append(lohSampleList)
        else:
            lohNbList = [nbLoh]
        # lohPointList.append((chrName, start, end, ))
        for sampleLohList in lohNbList:
            nbLoh = len(sampleLohList)
            percent = nbLoh * 100. / nbVal
            # print 'CHR', chrPos, chrName, start, end, nbLoh, percent
            outFh2.write([start, chrName, -percent])
            outFh2.write([end, chrName, -percent])
            # print '@@@@', chrPos, previousEnd, start, end, percent
            # if previousEnd and start != previousEnd + 1:
            # outFh2.write([previousEnd + 1, start-1, 0])
            sampleLohFh.write(
                [start, end, chrName, -percent, ','.join(sampleLohList)])

    def __createHistDataFileAndGetMaxValue(self, dataList, targetDir,
                                           ploidyDict, keyword,
                                           lohDataDict=None,
                                           lohDataDict2=None):
        baseName = '.'.join(os.path.basename(self.__ascatFile).split('.')[:-1])
        fileName = os.path.join(targetDir, '%s_%s_hist_%s.txt' %
                                (baseName, keyword, self.__suffix))
        outFh = CsvFileWriter(fileName)
        sampleFh = CsvFileWriter(FileNameGetter(fileName).get('_samples.txt'))
        sampleFh.write(['CNV key', 'chrName', 'start',
                        'segmentLength', 'percentage', 'samples'])
        maxValue = lohFileName = None
        if lohDataDict:
            lohFileName = os.path.join(targetDir, '%s_%s_hist_%s_cnLoh.txt' % (
                baseName, keyword, self.__suffix))
            outFh2 = CsvFileWriter(lohFileName)
            sampleLohFh = CsvFileWriter(
                FileNameGetter(lohFileName).get('_samples.txt'))
            sampleLohFh.write(
                ['start', 'end', 'chrName', 'percentage', 'samples'])
            lohFileName2 = os.path.join(
                targetDir, '%s_%s_hist_%s_loh.txt' % (baseName, keyword,
                                                      self.__suffix))
            lohFh = CsvFileWriter(lohFileName2)
            sampleLohFh2 = CsvFileWriter(
                FileNameGetter(lohFileName2).get('_samples.txt'))
            sampleLohFh2.write(
                ['start', 'end', 'chrName', 'percentage', 'samples'])
        dataList0 = dataList[0][-1]
        lohPointList = []
        previousEnd = previousChrName = None
        for i, (chrPos, currentPloidy) in enumerate(dataList0):
            ploidyList = []
            countDict = defaultdict(list)
            countDict2 = defaultdict(int)
            nbVal = nbLoh = 0
            for sampleName, currentDataList in dataList:
                currentPloidy = currentDataList[i][1]
                defaultPloidy = ploidyDict[sampleName.split('_')[0]]
                currentPloidy -= defaultPloidy
                ploidyKey = self.__getPloidyKeyFromPloidy(currentPloidy)
                nbVal += 1
                countDict[ploidyKey].append(sampleName)
            chrName, pos = chrPos.split(':')
            start, end = pos.split('-')
            start = int(start)
            end = int(end)
            if lohDataDict:
                if previousChrName and chrName != previousChrName:
                    previousEnd = None
                if i != len(dataList0) - 1 and previousEnd and \
                   start != previousEnd + 1:
                    print chrPos, previousEnd, previousChrName, start, end
                    outFh2.write([previousEnd + 1, chrName, 0])
                    outFh2.write([start - 1, chrName, 0])
                    lohFh.write([previousEnd + 1, chrName, 0])
                    lohFh.write([start - 1, chrName, 0])
                self.__writeLOH(lohDataDict, outFh2, nbVal,
                                chrPos, chrName, start, end, sampleLohFh)
                self.__writeLOH(lohDataDict2, lohFh, nbVal,
                                chrPos, chrName, start, end, sampleLohFh2)
                previousEnd = end
                previousChrName = chrName
            for key, sampleList in countDict.iteritems():
                val = len(sampleList)
                val = val * 100. / nbVal
                countDict2[key] = val
            # countDict = {valueDict[currentPloidy]: 100}

            histStart = start + (end - start) / 2
            length = end - start + 1
            # Order of outputs bellow is VERY important for color coding in the
            # histogram !
            currentValue1 = currentValue2 = 0
            for i in range(1, 7):
                value = countDict2.get(i, 0)
                outFh.write([i, chrName, histStart, length, value])
                currentValue1 += value
                sampleFh.write([i, chrName, histStart, length,
                                value, ','.join(countDict[i])])
            for i in range(-1, -5, -1):
                value = countDict2.get(i, 0)
                outFh.write([i, chrName, histStart, length, -value])
                currentValue2 += value
                sampleFh.write(
                    [i, chrName, histStart, length, -value,
                     ','.join(countDict[i])])
            maxValue = max(maxValue, currentValue1, currentValue2)
        return fileName, maxValue, lohFileName, lohFileName2

    def __createReorderedPhenotypeFile(self, fileName, outFileName, colorDict):
        rColorDict = {'red': 2, 'green': 3, 'blue': 4, 'grey': 8}
        colorList = rColorDict.values()
        colorList.sort()
        colorList.sort()
        fh = ReadFileAtOnceParser(fileName)
        outFh = CsvFileWriter(outFileName)
        header = fh.getSplittedLine()
        outFh.write(header)
        outFh2 = CsvFileWriter(outFileName + '2')
        outFh2.write(header)
        groupIdx, sampleIdx = self.__getGroupAndSampleIdxFromHeader(header)
        lineDict = dict([[splittedLine[sampleIdx].split(
            '_')[0], splittedLine] for splittedLine in fh])
        for sampleName in Utilities.getOrderedKeys(lineDict):
            splittedLine = lineDict[sampleName]
            outFh.write(splittedLine)
            if not splittedLine[groupIdx]:
                continue
            group = splittedLine[groupIdx].split()[-1]
            # color = rColorDict[colorDict[group]]
            # for i in range(len(splittedLine)):
            # if i != 2:
            # splittedLine[i] = colorList.index(color) + 1
            # outFh2.write([sampleName] + [splittedLine[i] for i in
            # range(len(splittedLine)) if i != 2])
            outFh2.write([sampleName])

    def __createHistRscriptFile(self, targetDir, keyword, histDataFile,
                                maxValue, lohHistFileName, centromereDict,
                                lohHistFileName2, lohToPlot=None):
        # print 'MAX = [%f]' % maxValue
        # print lohPointList
        if not lohToPlot:
            lohToPlot = self._cnLOH
        maxValue = min(100, maxValue + 5)
        lohStr = lohGraphStr = ''
        if lohHistFileName:
            lohPlotStr = 'geom_line(data=dataLoh2, aes(x=V1, y=V3, color = \
"blue"), stat = "identity", size = 0.3)'
            cnLohPlotStr = 'geom_line(data=dataLoh, aes(x=V1, y=V3, color = \
"black"), stat = "identity", size = 0.3)'
            if lohToPlot == self._LOH:
                plotStr = lohPlotStr
                legendLabel = self._LOH
                colorList = ['blue']
                labelList = ['']
            elif lohToPlot == self._cnLOH:
                plotStr = cnLohPlotStr
                legendLabel = self._cnLOH
                colorList = ['black']
                labelList = ['']
            elif lohToPlot == self._BOTH:
                plotStr = cnLohPlotStr + ' + ' + lohPlotStr
                legendLabel = self._LOH
                colorList = ['black', 'blue']
                labelList = ['cn-LOH', 'LOH']
            else:
                raise NotImplementedError(
                    'unrecognized value "%s" for lohToPlot. Value should be \
one of ("LOH", "cn-LOH", "both")' % lohToPlot)
            lohGraphStr = '+ %s + scale_colour_manual(values = %s, labels = \
%s, name = "%s")' % (plotStr, R()._getStrFromList(colorList),
                     R()._getStrFromList(labelList), legendLabel)
            lohStr = 'dataLoh = read.table("%s")\ndataLoh2 = \
read.table("%s")' % (lohHistFileName, lohHistFileName2)
        # if lohPointList:
            # lohStr = 'lohDataFrame = data.frame(pos=c(%s), )' %
            # (','.join([str(start) for chrName, start, end, percent in
            # lohPointList]))
        centroPosList = [125000000, 93300000, 91000000, 50400000, 48400000,
                         61000000, 59900000, 45600000, 49000000, 40200000,
                         53700000, 35800000, 17900000, 17600000, 19000000,
                         36600000, 24000000, 17200000, 26500000, 27500000,
                         13200000, 14700000]
        if centromereDict:
            centroPosList = []
            for chrName in range(1, 23):
                start, end = centromereDict[str(chrName)]
                centroPosList.append((start + end) / 2)
        colorList = ['black', 'darkred', 'red', 'darkorange1',
                     'mediumseagreen', 'darkolivegreen4', 'steelblue4',
                     'royalblue4', 'purple', 'magenta']
        rColorList = self.__rColorDict.get('histogram')
        if rColorList:
            colorList = rColorList
        rFileName = os.path.join(targetDir, '%s_hist_%s.R' %
                                 (keyword, self.__suffix))
        colorStrList = ['%d" = "%s' % (cnvValue, colorList[i])
                        for i, cnvValue in enumerate(
                            [-4, -3, -2, -1, 1, 2, 3, 4, 5, 6])]
        # c("6" = "magenta", "5" = "purple", "4" = "royalblue4", "3" =
        # "steelblue4", "2" = "darkolivegreen4", "1" = "mediumseagreen", "-1" =
        # "darkorange1", "-2" = "red", "-3" = "darkred", "-4" = "black")
        rStr = """
library(ggplot2)
data = read.table("%(histDataFile)s")
%(lohStr)s
subdat1 = subset(data, V1>0)
subdat2 = subset(data, V1<0)
subdat2$V1 <- factor(subdat2$V1, levels = levels(factor(subdat2$V1)))
subdat1$V1 <- factor(subdat1$V1, levels = rev(levels(factor(subdat1$V1))))
centro = data.frame(pos=%(centroPosStr)s,
V2=c(1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22))
png("%(filePattern)s.png", width=4000, height=1800, res=300)
cols <- %(colorStr)s
if ("expand" %%in%% names(formals(coord_cartesian))) {
   yLim <- coord_cartesian(ylim=c(-%(yMax)d,%(yMax)d), expand = FALSE)
} else{
   yLim <- coord_cartesian(ylim=c(-%(yMax)d,%(yMax)d))
}
gr = ggplot(data=data)+ geom_hline(yintercept=-25, colour="white", size=0.5) +
facet_grid(~V2, space="free_x", scales="free_x", labeller=label_value)+
theme(axis.text.x=element_blank(), axis.ticks=element_blank(),
axis.title.x=element_blank(),text = element_text(size=15),
axis.text.y = element_text(size=15), legend.text = element_text(size=10),
legend.position = "bottom", legend.box = "horizontal") +
geom_bar(data=subdat1,aes(x=V3, y=V5, fill=factor(V1), width=V4),
stat="identity")+geom_bar(data=subdat2,aes(x=V3, y=V5, fill=V1, width=V4),
stat="identity") + guides(fill=guide_legend(title="CNV",keywidth = 1.5,
keyheight = 1.5, label.position="bottom", label.hjust=0.4, title.vjust=0.7,
reverse=TRUE, nrow = 1))+ylab("%%copy number gain / loss, copy neutral LOH")+
scale_fill_manual(values = cols, breaks = c(6, 5, 4, 3, 2, 1, -1, -2, -3, -4),
labels=c(expression("" >= 6),"5","4","3","2","1","-1","-2","-3",
expression("" <= -4))) + yLim +scale_x_continuous(breaks = NULL)+
geom_point(aes(x=pos, y=0),centro, size=1.5) +
geom_hline(yintercept=0, colour="black", size=0.5) %(lohGraphStr)s +
scale_y_continuous(breaks = c(50, 100, -50, -100, 0),
labels = c(50, 100, 50, 100, 0))
plot(gr)
dev.off()
""" % {'lohStr': lohStr, 'centroPosStr': R()._getStrFromList(centroPosList),
            'yMax': maxValue, 'lohGraphStr': lohGraphStr,
            'histDataFile': histDataFile,
            'filePattern': rFileName.replace('.R', ''),
            'colorStr': R()._getStrFromList(colorStrList)}
        outFh = CsvFileWriter(rFileName)
        outFh.write(rStr)
        return rFileName

    def __createHistogramForSample(self, dataList, ploidyDict, sampleName,
                                   targetDir, lohDataDict, centromereDict):
        histFileName, maxValue, lohHistFileName = \
            self.__createHistDataFileAndGetMaxValue(
                [(sampleName, dataList)], targetDir, ploidyDict,
                sampleName, lohDataDict)
        rFileName = self.__createHistRscriptFile(
            targetDir, sampleName, histFileName, maxValue, lohHistFileName,
            centromereDict)
        cmd = '%sRscript --vanilla %s' % (self.__binStr, rFileName)
        if self.__binDir:
            cmd = 'xvfb-run -w 60 --auto-servernum ' + cmd
        Utilities.mySystem(cmd)

    def __createMergedHistogram(self, matrixDict, ploidyDict, targetDir,
                                lohMatrixDict, keyword='',
                                getMaxValueOnly=False, maxValueToUse=None,
                                centromereDict=None, lohMatrixDict2=None,
                                lohToPlot=None):
        print 'Merged hist for "%s": %d samples: %s' % \
              (keyword, len(matrixDict), matrixDict.keys())
        dataList = []
        # NFPET=['P1T','P22T','P26T','P27T','P47T','P4T','P58T','P60T','P6T','P71T','P72T','P73T','P7T','P8T']
        lohDataDict = {}
        lohDataDict2 = {}
        dataSize = None
        for sampleName, chrDict in matrixDict.iteritems():
            sampleName = sampleName.split('.')[0]
            if sampleName[:2] == 'CS':  # or sampleName in NFPET:
                continue
            currentDataList = matrixDict[sampleName]
            dataList.append((sampleName, currentDataList))
            if not dataSize:
                dataSize = len(currentDataList)
            if dataSize != len(currentDataList):
                raise NotImplementedError
            if not lohMatrixDict:
                continue
            lohList = lohMatrixDict.get(sampleName)
            if lohList:
                lohDataDict[sampleName] = {}
                for chrPos, ploidy in lohList:
                    lohDataDict[sampleName][chrPos] = ploidy
                # lohDataList.append((sampleName, lohList))
            lohList2 = lohMatrixDict2.get(sampleName)
            if lohList2:
                lohDataDict2[sampleName] = {}
                for chrPos, ploidy in lohList2:
                    lohDataDict2[sampleName][chrPos] = ploidy
        histFileName, maxValue, lohHistFileName, lohHistFileName2 = \
            self.__createHistDataFileAndGetMaxValue(
                dataList, targetDir, ploidyDict, 'merged%s' % keyword,
                lohDataDict, lohDataDict2)
        if getMaxValueOnly:
            return maxValue
        if maxValueToUse:
            maxValue = maxValueToUse
        rFileName = \
            self.__createHistRscriptFile(
                targetDir, '%s_merged%s' % ('.'.join(os.path.basename(
                    self.__ascatFile).split(
                    '.')[:-1]), keyword), histFileName, maxValue,
                lohHistFileName, centromereDict, lohHistFileName2,
                lohToPlot)
        cmd = '%sRscript --vanilla %s' % (self.__binStr, rFileName)
        if self.__binStr:
            cmd = 'xvfb-run --auto-servernum ' + cmd
        Utilities.mySystem(cmd)

    def __createHistogramBySample(self, matrixDict, ploidyDict, targetDir,
                                  lohMatrixDict, centromereDict):
        cluster = ThreadManager(_getNbAvailableCpus())
        for sampleName, chrDict in matrixDict.iteritems():
            if sampleName[:2] == 'CS' or 'P51T' not in sampleName:
                continue
            sampleName = sampleName.split('.')[0]
            print 'Processing sample %s' % sampleName
            lohDataDict = {}
            lohList = lohMatrixDict.get(sampleName)
            if lohList:
                lohDataDict[sampleName] = {}
                for chrPos, ploidy in lohList:
                    lohDataDict[sampleName][chrPos] = ploidy
                print 'LOH'
                print lohDataDict
            cluster.submit(Utilities()._runFunc,
                           self.__createHistogramForSample, [matrixDict[
                               sampleName], ploidyDict, sampleName, targetDir,
                lohDataDict],
                os.path.join(targetDir, '%s_hist2_%s' %
                                        (sampleName, self.__suffix)))
            # break
        cluster.wait()

    def __getGroupAndSampleIdxFromHeader(self, header):
        groupIdx = None
        header = [columnName.lower() for columnName in header]
        for i, columnName in enumerate(header):
            if 'classification' in columnName or columnName == 'group':
                groupIdx = i
                break
        return groupIdx, header.index('sample')

    def __getShapeList(self, getRcode=False):
        # check http://www.endmemo.com/program/R/pchsymbols.php for full list
        # of R shapes
        # shapeDict = {'diamond': 18, 'circle': 16, 'triangle': 17, 'square':
        # 15, 'triangle2': 25}#, 'plus': 3, 'cross': 4, 'star': 8}
        shapeList = [('diamond', 18), ('circle', 16),
                     ('triangle', 17), ('square', 15), ('triangle2', 25)]
        if getRcode:
            return [shapeCode for shapeName, shapeCode in shapeList]
        return [shapeName for shapeName, shapeCode in shapeList]

    def _getGroupAndColorCodeDictFromFile(self, fileName,
                                          defaultGroupValue=None):
        fh = ReadFileAtOnceParser(fileName)
        header = fh.getSplittedLine()
        isCustom = False
        if 'group' not in header:
            isCustom = True
        groupIdx, sampleIdx = self.__getGroupAndSampleIdxFromHeader(header)
        if groupIdx is None:
            raise NotImplementedError(
                'Could not find group column from header = %s in file %s' %
                (str(header), fileName))
        groupDict = SimpleMultiDict()
        group2Dict = SimpleMultiDict()
        for splittedLine in fh:
            group = splittedLine[groupIdx]
            if not group:
                if defaultGroupValue:
                    group = defaultGroupValue
                else:
                    continue
            group = group.split()[-1]
            # groupDict[group] = splittedLine[1].split('_')[0]
            groupDict[group] = splittedLine[sampleIdx].strip()
            if isCustom:
                group2Dict[(4, splittedLine[groupIdx].strip())
                           ] = splittedLine[sampleIdx].strip()
                group2Dict[(3, splittedLine[3].strip())] = splittedLine[
                    sampleIdx].strip()
        for group in groupDict:
            sampleList = groupDict.getall(group)
            print group, len(sampleList), sampleList
        if isCustom:
            colorDict = {'Gastrinoma': 'green4', 'NF-PET': 'blue',
                         'SIET': 'grey', 'Insulinoma': 'red'}
            shapeDict = {'Gastrinoma': 'diamond', 'NF-PET': 'circle',
                         'SIET': 'triangle', 'Insulinoma': 'square'}
            shapeDict2 = {'Gastrinoma': 18, 'NF-PET': 16,
                          'SIET': 17, 'Insulinoma': 15}
        else:
            groupList = groupDict.keys()
            colorList = Color().getColorList(len(groupList))
            rColorList = self.__rColorDict.get('group')
            if rColorList and len(rColorList) >= len(groupList):
                colorList = rColorList[:len(groupList)]
            colorDict = dict([[groupList[i], color]
                              for i, color in enumerate(colorList)])
            # shapeDict = dict([[groupList[i], marker] for i, marker in
            # enumerate(self.__getShapeList()[:len(groupList)])])
            shapeDict = dict([[groupName, self.__getShapeList()[i % len(
                groupList)]] for i, groupName in enumerate(groupList)])
            shapeList = self.__getShapeList(True)[:len(groupList)]
            shapeDict2 = dict([[groupList[i], shape]
                               for i, shape in enumerate(shapeList)])
        return groupDict, group2Dict, colorDict, shapeDict, shapeDict2

    def __createSegments(self, fileName, ploidyFile, write=True, suffix=None,
                         shouldProcessFunc=None, targetDir=None):
        fh = ReadFileAtOnceParser(fileName)
        targetFileName = fileName + suffix
        if targetDir:
            targetFileName = os.path.join(targetDir, os.path.basename(targetFileName))
        outFh = CsvFileWriter(targetFileName)
        outFh.write(fh.getSplittedLine())
        ploidyDict = self._getPloidyDictFromFile(ploidyFile)
        segmentList = []
        for splittedLine in fh:
            sampleName = splittedLine[0].split('.')[0]
            # if sampleName[:2] == 'CS':
            # continue
            if sampleName not in ploidyDict:
                print 'Passing sample %s' % sampleName
                continue
            ploidy = ploidyDict[sampleName]
            if splittedLine[-1] == 'NA':
                continue
            nMin = float(splittedLine[-1])
            nMaj = float(splittedLine[-2])
            if shouldProcessFunc(nMin, nMaj, ploidy):
                if write:
                    outFh.write(splittedLine)
                else:
                    segmentList.append(splittedLine)
        if not write:
            return segmentList

    def _createLOHneutralSegments(self, fileName, ploidyFile, write=True,
                                  targetDir=None):
        def isCnLoh(nMin, nMaj, ploidy):
            return (nMaj, nMin) in [(-ploidy, 0), (ploidy, 0)]
        return self.__createSegments(fileName, ploidyFile, write,
                                     '_lohNeutral.txt', isCnLoh, targetDir)

    def _createLOH_Segments(self, fileName, ploidyFile, write=True, targetDir=None):
        def isLoh(nMin, nMaj, ploidy):
            return 0 in [nMaj, nMin]
        return self.__createSegments(fileName, ploidyFile, write, '_loh.txt',
                                     isLoh, targetDir = targetDir)

    def __getGroupStrColSideColorsStrAndLegendStrForHeatmap(self, groupDict,
                                                            groupColName=None,
                                                            idx=None,
                                                            groupList=None):
        if not groupDict:
            return '', '', ''
        if groupColName is None:
            groupColName = self.__groupColumnName
        nbValues = len(set(groupDict.values()))
        colorList = Color().getColorList(nbValues)
        rColorList = self.__rColorDict.get('group')
        if rColorList and len(rColorList) >= nbValues:
            colorList = rColorList[:nbValues]
        groupStr = 'colorDict <- list('
        legendList = []
        legendColorList = []
        if not groupList:
            groupList = groupDict.keys()
        for i, groupName in enumerate(groupList):
            color = colorList[i]
            legendList.append(groupName)
            legendColorList.append(color)
            for sampleName in groupDict.getall(groupName):
                groupStr += '"%s" = "%s", ' % (sampleName, color)
        groupStr = groupStr[:-2] + ')\n\n'
        if idx is None:
            idx = ''
        groupStr += '''colColorList%(idx)s <- c()
for (sampleName in rownames(a)){
    colColorList%(idx)s <- c(colColorList%(idx)s, colorDict[[sampleName]])
}''' % {'idx': ''}
        legendStr = '''legend("topright", xpd=TRUE, # location of the legend on the heatmap plot
    legend = %s, xpd=TRUE, # category labels
    col = %s,  # color key
    lty= 1,             # line style
    lwd = 5,            # line width
    title = "%s")

''' % (R()._getStrFromList(legendList), R()._getStrFromList(legendColorList),
            groupColName)
        if idx == '':
            return groupStr,\
                ', ColSideColors = colColorList, lhei = c(2.5, 5)',\
                legendStr
        return groupStr, ', ColSideColors = colColorList, lhei = c(2.5, 5)',\
            legendStr, legendList, legendColorList

    def __getCoordinateFromStr(self, posStr, defaultPosition):
        if not posStr:
            posStr = defaultPosition
        if ',' in posStr:
            posStr = [float(value.strip()) for value in posStr.split(',')]
            if len(posStr) != 2:
                raise NotImplementedError(
                    'Expecting coordinates to be 2 values but found %d: %s' %
                    (len(posStr), str(posStr)))
            posStr = '{0}, {1}'.format(*posStr)
        else:
            posStr.strip('"')
            posStr = '"%s"' % posStr
        return posStr

    def __getRowSideColorsStr(self, chrLegendPos):
        # colorList = Color().getColorList(22)
        # colorList.sort()
        # print colorList
        chrLegendPosStr = self.__getCoordinateFromStr(
            chrLegendPos, 'bottomleft')
        colorList = ["#82E291", "#03FE35", "#05C523", "#079C01", "#0C5234",
                     "#0E2912", "#897F92", "#87A8B4", "#4DCBF4", "#4F92E2",
                     "#4179C0", "#463FE3", "#4806D1", "#8B4680", "#801CA3",
                     "#8E35C5", "#C81285", "#C52340", "#C35C52", "#C18574",
                     "#CAE863", "#CCBF41"]
        rColorList = self.__rColorDict.get('chr')
        if rColorList:
            colorList = rColorList
        rStr = 'rowColorDict <- list('
        legendList = []
        legendColorList = []
        for i, chrName in enumerate(range(1, 23)):
            color = colorList[i]
            rStr += 'X%s = "%s", ' % (chrName, color)
            legendList.append(str(chrName))
            legendColorList.append(color)
        rStr = rStr[:-2] + ')\n\n'
        rStr += '''rowColorList <- c()
for (pos in colnames(a)){
    partList <- strsplit(pos, "[.]")
    rowColorList <- c(rowColorList, rowColorDict[[partList[[1]][1]]])
}'''
        legendStr = '''legend(%s,      # location of the legend on the heatmap plot
    legend=%s, # category labels
    col=%s,  # color key
    lty=1,             # line style
    lwd=5,            # line width
    title="Chromosomes", box.lty=0)''' % (chrLegendPosStr,
                                          R()._getStrFromList(legendList),
                                          R()._getStrFromList(legendColorList))
        return rStr, legendStr

    def __createHeatmap2(self, matrixFile, hclustStr, height, width, cexRow,
                         cexCol, margins, labRowStr, labColStr, groupDict,
                         hclust, groupLegendPos, chrLegendPos):
        # groupStr, colColorStr, legendStr =
        # self.__getGroupStrColSideColorsStrAndLegendStrForHeatmap(groupDict)
        groupStr = self.__getColorRstr(matrixFile, hclust)
        rowSideStr, rowLegendStr = self.__getRowSideColorsStr(chrLegendPos)
        rowColorStr = ', RowSideColors = rowColorList'
        # labRow = %(labRow)s, labCol = %(labCol)s
        groupLegendPosStr = self.__getCoordinateFromStr(
            groupLegendPos, 'topright')
        colorList = ['red', 'orange', 'yellow', 'green', 'deepskyblue',
                     'blue', 'purple3', 'magenta', 'orchid1', 'black']
        rColorList = self.__rColorDict.get('heatmap')
        if rColorList:
            colorList = rColorList
        rStr = '''library("RColorBrewer")
library("gplots")

source("%(dirName)s/heatmap.2.2.R")

a<-(as.matrix(read.csv2("%(inputFile)s", h=T, row.names=1, sep="\t")))

hr <- hclust(dist(t(a)))
hc <- hclust(dist(a))

%(groupStr)s

%(rowSideStr)s

for (colColorList in allColColorList){
    print(paste("Generating heatmap for colName <", colColorList$title, ">"))
    pdf(colColorList$outFileName, height=%(height)d, width=%(width)d)
    #heatmap.2(t(a), Rowv=as.dendrogram(hr), Colv=as.dendrogram(hc),
    #margins=%(marginStr)s, key=TRUE, symkey=FALSE, density.info="histogram",
    #denscol="black", trace="none", scale="none", col=c(c("red", "orange",
    #"yellow", "green", "deepskyblue", "blue", "purple3", "magenta",
    #"orchid1"), rep("black", max(a)-8)), cexRow=%(cexRow)f, cexCol=%(cexCol)f,
    #ColSideColors = colColorList$colColorList%(hclustStr)s
    #%(labRowStr)s%(labColStr)s%(rowColorStr)s)

    colorList <- %(colorStr)s
    #col=c(c("red", "orange", "yellow", "green", "deepskyblue", "blue",
    #"purple3", "magenta", "orchid1"), rep("black", max(a)-8))
    heatmap.2.1(t(a), Rowv=as.dendrogram(hr), Colv=as.dendrogram(hc),
    margins=%(marginStr)s, key=TRUE, symkey=FALSE, denscol="gray25",
    lhei = c(2.5, 5), key.xlab = "CNV value", trace="none", scale="none",
    col=c(colorList[1:9], rep(colorList[10], max(a)-8)), cexRow=%(cexRow)f,
    cexCol=%(cexCol)f, ColSideColors =
    colColorList$colColorList%(hclustStr)s%(labRowStr)s%(labColStr)s
    %(rowColorStr)s)

    #h <- hist(t(a), plot = FALSE, breaks=breaks)
            #hx <- scale01(breaks, min.raw, max.raw)
            #hy <- c(h$counts, h$counts[length(h$counts)])
            #lines(hx, hy/max(hy) * 0.95, lwd = 1, type = "s",
                #col = denscol)
            #if (is.null(key.ytickfun)) {
                #yargs <- list(at = pretty(hy)/max(hy) * 0.95,
                  #labels = pretty(hy))
            #}
            #else {
                #yargs <- key.ytickfun()
            #}
            #yargs$side <- 2
            #do.call(axis, yargs)
            #if (is.null(key.title))
                #key.title <- "Color Key
#and Histogram"
            #if (!is.na(key.title))
                #title(key.title)
            #par(cex = 0.5)
            #if (is.null(key.ylab))
                #key.ylab <- "Count"
            #if (!is.na(key.ylab))
                #mtext(side = 2, key.ylab, line = par("mgp")[1], padj = 0.5,
                #cex = par("cex") * par("cex.lab"))

    par(cex = .8)
    # location of the legend on the heatmap plot
    legend(%(groupLegendPos)s, xpd=TRUE,
    legend = colColorList$legendList, # category labels
    col = colColorList$legendColorList,  # color key
    lty= 1,             # line style
    lwd = 5,            # line width
    title = colColorList$title)

    %(legendStr)s

    dev.off()
}
'''
        rStr = rStr % {'hclustStr': hclustStr, 'height': height,
                       'width': width, 'inputFile': matrixFile,
                       'cexRow': cexRow, 'cexCol': cexCol,
                       'marginStr': R()._getStrFromList(margins),
                       'groupStr': groupStr, 'legendStr': rowLegendStr,
                       'labRowStr': labRowStr, 'labColStr': labColStr,
                       'rowSideStr': rowSideStr, 'rowColorStr': rowColorStr,
                       'dirName': os.path.dirname(os.path.abspath(__file__)),
                       'groupLegendPos': groupLegendPosStr,
                       'colorStr': R()._getStrFromList(colorList)}
        R().runCmd(rStr, FileNameGetter(matrixFile).get('_heatmap_%s.R' %
                                                        hclust))

    def __createHeatmap(self, matrixFile, hclustStr, height, width, cexRow,
                        cexCol, margins, labRowStr, labColStr, groupDict,
                        outFileName):
        groupStr, colColorStr, legendStr = \
            self.__getGroupStrColSideColorsStrAndLegendStrForHeatmap(
                groupDict)
        rowSideStr, rowLegendStr = self.__getRowSideColorsStr()
        rowColorStr = ', RowSideColors = rowColorList'
        # labRow = %(labRow)s, labCol = %(labCol)s
        rStr = '''pdf("%(outFileName)s", height=%(height)d, width=%(width)d)

library("RColorBrewer")
library("gplots")
a<-(as.matrix(read.csv2("%(inputFile)s", h=T, row.names=1, sep = "\t")))

#hr <- hclust(dist(t(a)))
#hc <- hclust(dist(a)) Rowv=as.dendrogram(hr), Colv=as.dendrogram(hc),

%(groupStr)s

%(rowSideStr)s

heatmap.2(t(a), margins=%(marginStr)s, key=TRUE, symkey=FALSE,
density.info="histogram", denscol="gray25", key.xlab = "CNV value",
lhei = c(2.5, 5), trace="none", scale="none", col=c(c("red", "orange",
"yellow", "green", "deepskyblue", "blue", "purple3", "magenta", "orchid1"),
rep("black", max(a)-8)), cexRow=%(cexRow)f, cexCol=%(cexCol)f,
%(hclustStr)s%(colColorStr)s%(labRowStr)s%(labColStr)s%(rowColorStr)s)

par(cex = .5)
%(legendStr)s

dev.off()
'''
        rStr = rStr % {'hclustStr': hclustStr, 'outFileName': outFileName,
                       'height': height, 'width': width,
                       'inputFile': matrixFile, 'cexRow': cexRow,
                       'cexCol': cexCol, 'marginStr': R()._getStrFromList(
                           margins), 'groupStr': groupStr,
                       'colColorStr': colColorStr,
                       'legendStr': legendStr + rowLegendStr,
                       'labRowStr': labRowStr, 'labColStr': labColStr,
                       'rowSideStr': rowSideStr, 'rowColorStr': rowColorStr}
        R().runCmd(rStr, FileNameGetter(outFileName).get('R'))

    def __getGroupIdxFromValue(self, value, minValue, step):
        groupIdx = (float(value) - minValue) / step
        if int(groupIdx) != groupIdx:
            groupIdx = int(groupIdx) + 1
        else:
            groupIdx = int(groupIdx)
        return groupIdx

    def __getGroupListAndValueToGroupDictFromValueList(self, valueList,
                                                       valueDict):
        valueList.sort()
        groupList = []
        if 'NA' in valueDict.values():
            groupList.append('NA')
        nbEltsPerGroup = int(math.ceil(len(valueList) / 4.))
        valueToGroupDict = {}
        for i in range(4):
            currentValueList = valueList[
                i * nbEltsPerGroup: (i + 1) * nbEltsPerGroup + 1]
            if i == 3:
                groupName = 'expression(paste(%f <= X, "" <= %f))' % (
                    currentValueList[0], currentValueList[-1])
            else:
                groupName = 'expression(paste(%f <= X, " < ", %f))' % (
                    currentValueList[0], currentValueList[-1])
                currentValueList.remove(currentValueList[-1])
            groupList.append(groupName)
            for value in currentValueList:
                valueToGroupDict[value] = groupName
        return groupList, valueToGroupDict

    def __getColorDictAndGroupListFromValueDict(self, valueDict, colorList):
        valueList = list(set(valueDict.values()))
        nonEmptyValueList = [
            value for value in valueList if value not in ['', 'NA']]
        valueList.sort()
        colorDict = {}
        groupList = []
        # print ':' * 50
        # print valueList
        # print '?' * 50
        if len(valueList) < 10 or \
           (not ValueParser().isFloat(nonEmptyValueList[0]) and
                not ValueParser().isFloat(nonEmptyValueList[1])):
            groupList = valueList
            for i, (sampleName, value) in enumerate(valueDict.iteritems()):
                color = colorList[valueList.index(value)]
                colorDict[sampleName] = color, value
        else:
            # groupList = []
            # if 'NA' in valueList:
                # groupList.append('NA')
            valueList = [float(value) for value in nonEmptyValueList]
            # minValue = min(valueList)
            # maxValue = max(valueList)
            # step = (maxValue - minValue) / 4.
            groupList, valueToGroupDict = \
                self.__getGroupListAndValueToGroupDictFromValueList(
                    valueList, valueDict)
            # for i in range(4):
            # groupList.append('%f expression(""<=) X < %f' %
            # (minValue + i * step, minValue + (i+1) * step))
            for i, (sampleName, value) in enumerate(valueDict.iteritems()):
                if value == 'NA':
                    groupIdx = 0
                else:
                    groupName = valueToGroupDict[float(value)]
                    # groupIdx = self.__getGroupIdxFromValue(value, minValue,
                    # step)
                    groupIdx = groupList.index(groupName)
                color = colorList[groupIdx]
                try:
                    colorDict[sampleName] = color, groupList[groupIdx]
                except:
                    print sampleName, groupIdx, value, step, minValue,\
                        maxValue, groupList
                    raise
        return colorDict, groupList

    def __getHeaderFromFh(self, fh):
        for splittedLine in fh:
            if not splittedLine[0].strip() or splittedLine[0][0] == '#':
                continue
            header = splittedLine
            return header

    def __getSampleIdxFromHeader(self, header):
        try:
            sampleIdx = header.index('Sample')
        except ValueError:
            sampleIdx = header.index('Sample ID')
        if 'sampleAlias' in header:
            sampleIdx = header.index('sampleAlias')
        return sampleIdx

    def __getGroupDictDictAndGroupListDict(self):
        fh = ReadFileAtOnceParser(self.__sampleFile)
        header = self.__getHeaderFromFh(fh)
        groupDict = defaultdict(dict)
        sampleIdx = self.__getSampleIdxFromHeader(header)
        sampleList = []
        for splittedLine in fh:
            for i, colName in enumerate(header):
                if i == sampleIdx or colName in ['Sample', 'Sample ID']:
                    continue
                if self.__groupColumnName and \
                   colName != self.__groupColumnName:
                    continue
                value = splittedLine[i].strip()
                sampleName = splittedLine[sampleIdx]
                if value == '':
                    value = 'NA'
                value = value.replace('"', '')
                # if value == 'NA':
                # print '>' * 50
                # print splittedLine[sampleIdx], value
                groupDict[colName][sampleName] = value
        colorDict = defaultdict(dict)
        groupListDict = {}
        for colName in groupDict:
            currentGroupDict = groupDict[colName]
            colorList = Color().getColorList(
                len(set(currentGroupDict.values())))
            try:
                currentColorDict, groupList = \
                    self.__getColorDictAndGroupListFromValueDict(
                        currentGroupDict, colorList)
            except:
                print 'COLname = %s' % colName
                raise
            # colorDict[colName] = currentColorDict, groupList
            for sampleName, (color, groupName) in currentColorDict.iteritems():
                colorDict[sampleName][colName] = color, groupName
            groupListDict[colName] = groupList
        groupDict = defaultdict(SimpleMultiDict)
        for sampleName in colorDict:
            currentColorDict = colorDict[sampleName]
            for colName in currentColorDict:
                color, groupName = currentColorDict[colName]
                groupDict[colName][groupName] = sampleName
        return groupDict, groupListDict

    def __getColorRstr(self, matrixFile, hclust):
        groupDict, groupListDict = self.__getGroupDictDictAndGroupListDict()
        rStr = ''
        for i, colName in enumerate(groupDict.keys()):
            groupStr, colSideColorsStr, legendStr, legendList,\
                legendColorList = \
                self.__getGroupStrColSideColorsStrAndLegendStrForHeatmap(
                    groupDict[colName], colName, i, groupListDict[colName])
            groupStr += '\ncolColorList%(i)d <- c()\n\
colColorList%(i)d$colColorList <- colColorList\ncolColorList%(i)d$outFileName\
 <- "%(outFileName)s"\ncolColorList%(i)d$legendList <- %(legendList)s\n\
colColorList%(i)d$legendColorList <- %(legendColorList)s\ncolColorList%(i)d\
$title <- "%(title)s"\n' % {'i': i,
                            'legendList': R()._getStrFromList(legendList),
                            'legendColorList': R()._getStrFromList(
                                legendColorList),
                            'outFileName': FileNameGetter(matrixFile).get(
                                '_heatmap_%s_%s.pdf' % (colName.replace(
                                    ' ', '_').replace('/', '-'), hclust)),
                            'title': colName}
            rStr += groupStr
        rStr += '\nallColColorList <- list(%s)\n' % (
            ', '.join(['colColorList%d' % i for i in range(len(groupDict))]))
        return rStr

    def __createRGroupColorFile(self):
        colorFile = FileNameGetter(self.__sampleFile).get('_color.txt')
        colorFh = CsvFileWriter(colorFile)
        fh = ReadFileAtOnceParser(self.__sampleFile)
        header = fh.getSplittedLine()
        groupDict = defaultdict(dict)
        sampleIdx = header.index('Sample')
        if 'sampleAlias' in header:
            sampleIdx = header.index('sampleAlias')
        sampleList = []
        for splittedLine in fh:
            for i, colName in enumerate(header):
                if i == sampleIdx or colName == 'Sample':
                    continue
                value = splittedLine[i].strip()
                sampleName = splittedLine[sampleIdx]
                if value == '':
                    value = 'NA'
                groupDict[colName][sampleName] = value
        colorDict = defaultdict(dict)
        colorFileHeader = ['sample']
        for colName in groupDict:
            currentGroupDict = groupDict[colName]
            colorList = Color().getColorList(len(currentGroupDict))
            try:
                currentColorDict, groupList = \
                    self.__getColorDictAndGroupListFromValueDict(
                        currentGroupDict, colorList)
            except:
                print 'COLname = %s' % colName
                raise
            # colorDict[colName] = currentColorDict, groupList
            for sampleName, (color, groupName) in currentColorDict.iteritems():
                colorDict[sampleName][colName] = color, groupName
            colorFileHeader += [colName, colName + '.group']
        colorFh.write(colorFileHeader)
        for sampleName, currentColorDict in colorDict.iteritems():
            lineToWrite = [sampleName]
            for colName in groupDict:
                lineToWrite += list(currentColorDict[colName])
            colorFh.write(lineToWrite)
        return colorFile
    
    def __installHeatmapPackages(self):
        R().installPackage('RColorBrewer')
        R().installPackage('gplots')
    
    def _createHeatmap(self, matrixFile, hclust=None, height=None, width=None,
                       cexRow=None, cexCol=None, margins=None, labRow=None,
                       labCol=None, groupDict=None, groupLegendPos=None,
                       chrLegendPos=None):
        #self.__installHeatmapPackages()
        matrixFile = os.path.abspath(matrixFile)
        if not height:
            height = 12
        if not width:
            width = 10
        if not cexRow:
            cexRow = 0.45
        if not cexCol:
            cexCol = 0.7
        if not margins:
            margins = [5, 5]
        if labRow is None:
            labRow = False
        if labCol is None:
            labCol = True
        hclustStr = labRowStr = labColStr = ''
        if not labRow:
            labRowStr = ', labRow = FALSE'
        if not labCol:
            labColStr = ', labCol = FALSE'
        if hclust:
            hclustStr = ", hclustfun = function(x) hclust(x,method = '%s')" % \
                hclust
        # if groupDict:
            # groupDictList = [(groupDict, FileNameGetter(matrixFile).get(
            # '_heatmap_%s.pdf' % hclust))]
        # else:
            # colorFile = self.__createRGroupColorFile()
            # return
        # for groupDict, outFileName in groupDictList:
        # self.__createHeatmap(matrixFile, hclustStr, height, width, cexRow,
        # cexCol, margins, labRowStr, labColStr, groupDict, outFileName)
        self.__createHeatmap2(matrixFile, hclustStr, height, width, cexRow,
                              cexCol, margins, labRowStr, labColStr, groupDict,
                              hclust, groupLegendPos, chrLegendPos)

    def __getSampleShapeAndColorFunctionStr(self, groupDict, groupList):
        colorFuncStr = shapeFuncStr = ''
        colorList = Color().getColorList(len(groupList))
        rColorList = self.__rColorDict.get('group')
        if rColorList and len(rColorList) >= len(groupList):
            colorList = rColorList[:len(groupList)]
        shapeList = self.__getShapeList()
        shapeDict = dict([[groupName, shapeList[i % len(shapeList)]]
                          for i, groupName in enumerate(groupList)])
        shapeDict2 = dict([[groupName, self.__getShapeList(
            True)[i % len(shapeList)]] for i, groupName in
            enumerate(groupList)])
        for i, groupName in enumerate(groupList):
            sampleList = groupDict.getall(groupName)
            if self.__useShape:
                color = colorList[i]
                colorFuncStr += '\tif (sampleName %%in%% c(%s)) {return \
("%s")}\n' % (', '.join(['"%s"' % sampleName.split('_')[0] for sampleName in
                         sampleList]), color)
                shape = shapeDict[groupName]
                shapeFuncStr += '\tif (sampleName %%in%% c(%s)) {return \
("%s")}\n' % (', '.join(['"%s"' % sampleName.split('_')[0] for sampleName in
                         sampleList]), shape)
        return 'getShapeForSample <- function(sampleName) {\n%s\n}\n' % \
               (shapeFuncStr), 'getColorForSample <- function(sampleName) \
{\n%s\n}\n' % (colorFuncStr), colorList, [shapeDict2[groupName] for groupName
                                          in groupList]

    def __installPlotrixFromHomePage(self):
        R(self.__binDir, libDir=self.__rLibDir).installPackageFromUrl(
            'https://cran.r-project.org/web/packages/plotrix/index.html')

    def _createDendrogram(self, matrixFile, groupDict, colorDict, shapeDict,
                          ploidyFile2, shapeDict2, coeff, keyword=None,
                          defaultEmptyValue=None, hclust=None):
        groupDict, groupListDict = self.__getGroupDictDictAndGroupListDict()
        matrixFile = os.path.abspath(matrixFile)
        if not R(self.__binDir,
                 libDir=self.__rLibDir).isPackageInstalled('plotrix'):
            try:
                R(self.__binDir,
                  libDir=self.__rLibDir).installPackage('plotrix')
            except:
                self.__installPlotrixFromHomePage()
        colorFuncStr = shapeFuncStr = setLabelStr = getShapeFuncStr = \
            legendStr = ''
        colName = 'lab.col'
        adjustY = 'yMax * %f / 3' % coeff
        # print groupDict
        # if not adjustShapeSize:
        # adjustY = '1'
        # for groupName in groupDict:
        # sampleList = groupDict.getall(groupName)
        # if self.__useShape:
        # color = colorDict[groupName]
        # colorFuncStr += 'if (sampleName %%in%% c(%s)) {return ("%s")}\n' %
        # (', '.join(['"%s"' % sampleName.split('_')[0] for sampleName in
        # sampleList]), color)
        # shape = shapeDict[groupName]
        # shapeFuncStr += 'if (sampleName %%in%% c(%s)) {return ("%s")}\n' %
        # (', '.join(['"%s"' % sampleName.split('_')[0] for sampleName in
        # sampleList]), shape)
        shapeStr = drawShapeStr = ''
        if not hclust:
            hclust = 'ward'
        if self.__useShape:
            legendStr = 'legend("topright", "(x,y)", legend=obj$legend, \
col=obj$col, pch=obj$shape, title = obj$title)'
            colName = 'col'
            shapeStr = ', pch = getShapeForSample(label)'
            setLabelStr = 'attr(x, "label") <- ""'
            # getShapeFuncStr = 'getShapeForSample <- function(sampleName)
            # {\n    %s\n}' % shapeFuncStr
            drawShapeStr = '''for (sampleName in rownames(m[sample.ord, ])) {
        drawShape(x, obj$getShapeForSample(sampleName),
        obj$getColorForSample(sampleName), yMax)
        x <- x+1
    }
'''

        drawShapeFuncStr = '''library(graphics)
library(plotrix%(libStr)s)

drawShape <- function (x, shape, color, yMax) {
    downCoeff = %(adjustY)s
    if (shape == "circle") {
        draw.circle(x, -3 * downCoeff, 0.2, border=color, col=color)
    }
    else if (shape == "square") {
        rect(x-.2, -4.3 * downCoeff, x+.2, -1.5 * downCoeff, col=color,
        border=color)
    }
    else if (shape == "diamond") {
        polygon(c(x, x+.2, x, x-.2), c(-4.3 * downCoeff, -2.9 * downCoeff,
        -1.5 * downCoeff, -2.9 * downCoeff), col=color, border=color)
    }
    else if (shape == "triangle") {
        polygon(c(x-.2, x+.2, x), c(-4.3 * downCoeff, -4.3 * downCoeff,
        -1.5 * downCoeff), col=color, border=color)
    }
    else if (shape == "triangle2") {
        polygon(c(x-.2, x+.2, x), c(-1.5 * downCoeff, -1.5 * downCoeff,
        -4.3 * downCoeff), col=color, border=color)
    }
    #else if (shape == "triangle2") {
    #    plot(x, y, type = "l", ylim = c(-3, 3), main = "rotatexy",
    #col = cols[1], lwd = 2, xlim = c(-1, 7))
    #}
    else {
        stop(paste(c("Shape ", shape, " unhandled")))
    }
}

## function to set label color
labelCol <- function(x) {
  if (is.leaf(x)) {
    ## fetch label
    label <- attr(x, "label")
    %(setLabel)s
    ## set label color to red for A and B, to blue otherwise
  }
  return(x)
}

a <- read.csv2("%(inputFile)s", h=T, row.names=1, sep = "\t")
b <- as.matrix(a)
c <- dist(b)
d <- hclust(c, method="%(hclustStr)s")

## apply labelCol on all nodes of the dendrogram
d <- dendrapply(as.dendrogram(d), labelCol)
sample.ord <- order.dendrogram(d)
m <- b
''' % {'adjustY': adjustY, 'libStr': R(libDir=self.__rLibDir).getLibStr(),
            'inputFile': matrixFile, 'setLabel': setLabelStr,
            'hclustStr': hclust}

        rStr = drawShapeFuncStr
        for i, (colName, groupList) in enumerate(groupListDict.iteritems()):
            getShapeFuncStr, getColorFuncStr, colorList, shapeList = \
                self.__getSampleShapeAndColorFunctionStr(
                    groupDict[colName], groupList)
            imgFile = FileNameGetter(matrixFile).get(
                '_dendro_%s.png' % colName.replace(' ', '_'))
            if keyword:
                imgFile = FileNameGetter(imgFile).get('_%s.png' % keyword)
            rStr += '''%(getColorFuncStr)s

%(getShapeFuncStr)s

obj%(idx)d <- c()
obj%(idx)d$getShapeForSample <- getShapeForSample
obj%(idx)d$getColorForSample <- getColorForSample
obj%(idx)d$outFileName <- "%(pngFile)s"
obj%(idx)d$col <- %(colStr)s
obj%(idx)d$legend <- %(legendStr)s
obj%(idx)d$shape <- %(shapeStr)s
obj%(idx)d$title <- "%(title)s"
''' % {'pngFile': imgFile, 'getColorFuncStr': getColorFuncStr,
                'getShapeFuncStr': getShapeFuncStr, 'idx': i,
                'pngFile': imgFile,
                'colStr': R()._getStrFromList(colorList),
                'legendStr': R()._getStrFromList(groupList),
                'shapeStr': R()._getStrFromList(shapeList), 'title': colName}

        if len(groupListDict) == 1:
            rFileName = FileNameGetter(imgFile).get('R')
        else:
            rFileName = FileNameGetter(matrixFile).get('_dendro.R')
        rStr += '''    allFunctionList <- list(%(allFunctionStr)s)

for (obj in allFunctionList){
    print(paste("Generating dendrogram for colName <", obj$title, ">"))
    png(obj$outFileName, width=4000, height=2200, res=300)
    plot(d)
    xylim <- par("usr")
    plotdim <- par("pin")
    ymult <- getYmult()
    x = 1
    yMax = par('usr')[4]
    %(drawShapeStr)s
    %(legendStr)s
    dev.off()
}''' % {'drawShapeStr': drawShapeStr,
            'allFunctionStr': ', '.join(['obj%d' % i for i in
                                         range(len(groupListDict))]),
            'legendStr': legendStr}

        # outFh = CsvFileWriter(rFileName)
        # outFh.write(rStr)
        # outFh.close()
        # cmd = '%sRscript --vanilla %s %sout' % (self.__binStr, rFileName,
        # rFileName)
        # if self.__binStr:
        # cmd = 'xvfb-run -w 5 --auto-servernum ' + cmd
        # Utilities.mySystem(cmd)
        R(self.__binDir).runCmd(rStr, rFileName)

    def __createMatrixFile(self, matrixDict, targetDir, keyword='',
                           ploidyFile=None):
        try:
            from stats import getAvgAndStdDevFromList
        except ImportError:
            getAvgAndStdDevFromList = None
        outFileName = os.path.join(
            targetDir, 'matrix_%s%s.txt' % (keyword, self.__suffix))
        outFh = CsvFileWriter(outFileName)
        print '%d in matrixDict' % len(matrixDict)
        outHeader = ['Sample'] + [chrPos for chrPos,
                                  ploidy in matrixDict.values()[0]]
        outFh.write(outHeader)
        print 'ploidyFile = ', ploidyFile
        #raise NotImplementedError
        if ploidyFile:
            fh = ReadFileAtOnceParser(ploidyFile)
            header = fh.getSplittedLine()
            groupIdx, sampleIdx = self.__getGroupAndSampleIdxFromHeader(header)
            lineDict = dict([[splittedLine[sampleIdx].split(
                '_')[0], splittedLine] for splittedLine in fh])
        for sampleName in Utilities.getOrderedKeys(matrixDict):
            if sampleName[:2] == 'CS':
                continue
            if ploidyFile:
                splittedLine = lineDict[sampleName]
                if groupIdx is not None and not splittedLine[groupIdx]:
                    continue
            dataList = matrixDict[sampleName]
            ploidyLine = [ploidy for chrPos, ploidy in dataList]
            # if len(set(ploidyLine)) == 1:
            # print 'Excluding sample %s' % sampleName
            # continue
            # avg, std = getAvgAndStdDevFromList(ploidyLine)
            # if std < 1:
            # print 'Excluding sample %s based on std' % sampleName
            # continue
            if len(ploidyLine) + 1 != len(outHeader):
                print set(outHeader[1:]) - set([chrPos for chrPos, ploidy in
                                                dataList])
                print dataList
                print sampleName, len(outHeader), len(ploidyLine) + 1
                raise NotImplementedError
            outFh.write([sampleName] + ploidyLine)
        return outFileName

    def __getSubsetMatrixForSamples(self, matrixDict, sampleList):
        return dict([[sampleName, matrixDict.get(sampleName.split('_')[0])]
                     for sampleName in sampleList])

    def __createMergedHistograms(self, matrixDict, ploidyDict, targetDir,
                                 group2Dict, lohMatrixDict, groupDict,
                                 centromereDict, lohMatrixDict2, lohToPlot):
        cluster = ThreadManager(_getNbAvailableCpus())
        matrixDict = self.__getSubsetMatrixForSamples(
            matrixDict, [sampleName for sampleName in matrixDict if
                         sampleName[:2] != 'CS'])
        paramList = [(None, '')]
        if group2Dict:
            paramList += [(group2Dict.getall((3, 'PET')) +
                           group2Dict.getall((3, 'PET assimilated')), '_PET'),
                          (group2Dict.getall((3, 'SIET')), '_SIET'),
                          (group2Dict.getall((4, 'Insulinoma')),
                           '_Insulinoma'),
                          (group2Dict.getall((4, 'D. Gastrinoma')) +
                           group2Dict.getall((4, 'P. Gastrinoma')),
                           '_Gastrinoma'),
                          (group2Dict.getall((4, 'P. Gastrinoma')),
                           '_P_Gastrinoma'),
                          (group2Dict.getall((4, 'D. Gastrinoma')),
                           '_D_Gastrinoma'),
                          (group2Dict.getall((4, 'G1 NF-PET')) +
                           group2Dict.getall((4, 'G2 NF-PET')), '_NF-PET'),
                          (group2Dict.getall((4, 'G1 NF-PET')), '_NF-PET-G1'),
                          (group2Dict.getall((4, 'G2 NF-PET')), '_NF-PET-G2'),
                          (group2Dict.getall((4, 'G1 SIET')), '_SIET-G1'),
                          (group2Dict.getall((4, 'G2 SIET')), '_SIET-G2')
                          ]
        else:
            for groupName in groupDict:
                sampleList = groupDict.getall(groupName)
                paramList.append((sampleList, '_%s' % groupName))
        # paramList = [(group2Dict.getall((4, 'Insulinoma')), '_Insulinoma'),]
        # maxValue = None
        # for sampleList, keyword in paramList:
        #    subMatrix = matrixDict
        #    if sampleList:
        #        subMatrix = self.__getSubsetMatrixForSamples(matrixDict,
        # sampleList)
        #    maxValue = max(maxValue, self.__createMergedHistogram(subMatrix,
        # ploidyDict, targetDir, None, keyword, True))
        for sampleList, keyword in paramList:
            subMatrix = matrixDict
            subLohMatrix = lohMatrixDict
            subLohMatrix2 = lohMatrixDict2
            if sampleList:
                subMatrix = self.__getSubsetMatrixForSamples(
                    matrixDict, sampleList)
                subLohMatrix = self.__getSubsetMatrixForSamples(
                    lohMatrixDict, sampleList)
                subLohMatrix2 = self.__getSubsetMatrixForSamples(
                    lohMatrixDict2, sampleList)
            cluster.submit(self.__createMergedHistogram, subMatrix, ploidyDict,
                           targetDir, subLohMatrix, keyword, False, 95,
                           centromereDict, subLohMatrix2, lohToPlot)
        '''
        # all samples
        cluster.submit(self.__createMergedHistogram, matrixDict, ploidyDict,
        targetDir, lohMatrixDict)
        # PET + PET assimilated col D
        cluster.wait()
        return
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict,
        group2Dict.getall((3, 'PET')) + group2Dict.getall((3,
        'PET assimilated'))), ploidyDict, targetDir, '_PET')
        # SIET col D
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((3,
        'SIET'))), ploidyDict, targetDir, '_SIET')
        # Insulinoma col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'Insulinoma'))), ploidyDict, targetDir, '_Insulinoma')
        # D. Gastrinoma + P. Gastrinoma col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'D. Gastrinoma')) + group2Dict.getall((4, 'P. Gastrinoma'))),
        ploidyDict, targetDir, '_Gastrinoma')
        # P. Gastrinoma col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'P. Gastrinoma'))), ploidyDict, targetDir, '_P_Gastrinoma')
        # D. Gastrinoma col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'D. Gastrinoma'))), ploidyDict, targetDir, '_D_Gastrinoma')
        # G1 NF-PET + G2 NF-PET col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'G1 NF-PET')) + group2Dict.getall((4, 'G2 NF-PET'))), ploidyDict,
        targetDir, '_NF-PET')
        # G1 NF-PET col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'G1 NF-PET'))), ploidyDict, targetDir, '_NF-PET-G1')
        # G2 NF-PET col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'G2 NF-PET'))), ploidyDict, targetDir, '_NF-PET-G2')
        # G1 SIET col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'G1 SIET'))), ploidyDict, targetDir, '_SIET-G1')
        # G2 SIET col E
        cluster.submit(self.__createMergedHistogram,
        self.__getSubsetMatrixForSamples(matrixDict, group2Dict.getall((4,
        'G2 SIET'))), ploidyDict, targetDir, '_SIET-G2')'''
        cluster.wait()

    def __createReorderedPhenotypeFileFromMatrixFile(self, matrixFile,
                                                     ploidyFile, outFileName):
        sampleList = []
        fh = ReadFileAtOnceParser(matrixFile)
        header = fh.getSplittedLine()
        for splittedLine in fh:
            sampleName = splittedLine[0].split('_')[0]
            sampleList.append(sampleName)
        # print len(sampleList), sampleList
        del fh
        fh = ReadFileAtOnceParser(ploidyFile)
        outFh = CsvFileWriter(outFileName)
        outFh.write(fh.getSplittedLine()[2:])
        lineDict = dict([[splittedLine[1].split('_')[0], splittedLine]
                         for splittedLine in fh])
        for sampleName in sampleList:
            outFh.write([sampleName] + lineDict[sampleName][3:])

    def __getNextSegmentListInWindow(self, segmentList, sampleName, chrName,
                                     windowNb):
        nextSegmentList = []
        start = windowNb * self.__windowSize
        end = (windowNb + 1) * self.__windowSize - 1
        windowPos = Position(chrName, start, end)
        while segmentList:
            segment = segmentList.pop(0)
            if segment[:2] != [sampleName, chrName]:
                break
            segmentPos = Position(chrName, segment[2], segment[3])
            overlapPos = windowPos.getOverlapPosition(segmentPos)
            if overlapPos:
                nextSegmentList.append(overlapPos)
                if segmentPos.end > windowPos.end:
                    segment[2] = windowPos.end + 1
                    segmentList.insert(0, segment)
        return nextSegmentList

    def __getCentromereDictFromFile(self, fileName):
        centromereDict = {}
        fh = ReadFileAtOnceParser(fileName)
        for splittedLine in fh:
            chrName, start, end = splittedLine[:3]
            if chrName[:3] == 'chr':
                chrName = chrName[3:]
            start = int(start)
            end = int(end)
            info = centromereDict.get(chrName)
            if info:
                oldStart, oldEnd = info
                start = min(start, oldStart)
                end = max(end, oldEnd)
                if len(set([start, oldStart, end, oldEnd])) != 3:
                    print start, oldStart, end, oldEnd
                    raise NotImplementedError
            centromereDict[chrName] = start, end
        return centromereDict

    def __createFileWithTruncatedCentromereData(self, ascatFile,
                                                centromereFile,
                                                centromereDict, targetDir):
        outFileName = FileNameGetter(ascatFile).get(
            '_%s.txt' % os.path.basename(centromereFile).split('.')[0])
        if targetDir:
            outFileName = os.path.join(targetDir, os.path.basename(outFileName))
        outFh = CsvFileWriter(outFileName)
        fh = ReadFileAtOnceParser(ascatFile)
        outFh.write(fh.getSplittedLine())
        for splittedLine in fh:
            # print splittedLine
            pos = OrientedPosition(*(splittedLine[1:4] + ['+']))
            centromerePos = Position(pos.ctgId, *centromereDict[pos.ctgId])
            overlapPos = pos.getOverlapPosition(centromerePos)
            if overlapPos:
                covDiff = Coverage(pos).getCovDiffWithPosition(overlapPos)
                if isinstance(covDiff, types.TupleType):
                    covDiff1, covDiff2 = covDiff
                    splittedLine[2] = covDiff1.pos.start
                    splittedLine[3] = covDiff1.pos.end
                    outFh.write(splittedLine)
                    splittedLine[2] = covDiff2.pos.start
                    splittedLine[3] = covDiff2.pos.end
                else:
                    if not covDiff:
                        continue
                    splittedLine[2] = covDiff.pos.start
                    splittedLine[3] = covDiff.pos.end
            outFh.write(splittedLine)
        return outFileName

    def __isFileInLrrBafFormat(self, fileName):
        fh = ReadFileAtOnceParser(fileName, bufferSize=1)
        header = fh.getSplittedLine()
        return len(header) >= 4 and '.Log R Ratio' in header[3]

    def __isFileInAscatFormat(self, fileName):
        fh = ReadFileAtOnceParser(fileName, bufferSize=1)
        return fh.getSplittedLine() == ['sample', 'chr', 'startpos', 'endpos',
                                        'nMajor', 'nMinor']

    def __cleanDir(self, tmpDir):
        targetDir = os.path.dirname(tmpDir)
        for cmd in ['mv %s %s' % (os.path.join(tmpDir, '*.png'), targetDir),
                    'mv %s %s' % (os.path.join(tmpDir, '*.pdf'), targetDir),
                    'mv %s %s' % (os.path.join(tmpDir, '*_samples.txt'), targetDir)]:
            os.system(cmd + ' 2> /dev/null')
        aptOutDir = os.path.join(tmpDir, 'apt_out')
        if os.path.isdir(aptOutDir):
            currentTargetDir = os.path.join(targetDir, 'ASCAT')
            Utilities.mySystem('mkdir -p %s' % currentTargetDir)
            for cmd in ['mv %s %s' % (os.path.join(aptOutDir, '*.png'), currentTargetDir),
                        'mv %s %s' % (os.path.join(aptOutDir, '*.segments.txt'), currentTargetDir),
                        'mv %s %s' % (os.path.join(aptOutDir, '*.ascatInfo.txt'), currentTargetDir)]:
                Utilities.mySystem(cmd)
        for cmd in ['mv %s %s' % (os.path.join(tmpDir, '*.R'), targetDir),
                    'rm -rf %s' % tmpDir]:
            Utilities.mySystem(cmd)
    
    def process(self, ascatFile, chrFile, targetDir, ploidyFile,
                histogram=True, merge=False, dendrogram=False, plotAll=False,
                centromereFile=None, keyword=None, defaultGroupValue=None,
                mergeCentromereSegments=None, gcFile=None, platform=None,
                libDir=None, gw6Dir=None, snpFile=None, normalize=True,
                sampleList=None, heatmap=False, hclust=None, height=None,
                width=None, cexRow=None, cexCol=None, margins=None,
                labRow=None, labCol=None, groupLegendPos=None,
                chrLegendPos=None, fileType=None, keepCentromereData=False,
                lohToPlot=None):
        originalTargetDir = targetDir
        if targetDir:
            targetDir = os.path.join(targetDir, 'tmp')
            Utilities.mySystem('mkdir -p %s' % targetDir)
        if sampleList and os.path.isfile(sampleList[0]):
            sampleList = Utilities.loadCache(sampleList[0])
        if not os.path.isfile(ascatFile) or \
           (not self.__isFileInLrrBafFormat(ascatFile) and not
                self.__isFileInAscatFormat(ascatFile)):
            if os.path.isdir(ascatFile):
                dumpFileName = os.path.join(targetDir, 'ascatFile.pyDump')
            else:
                # os.path.dirname(ascatFile.split(',')[0])
                dumpFileName = os.path.join(targetDir, 'ascatFile_%d.pyDump' %
                    hash(ascatFile))
            if fileType == 'Sequenza':
                if not os.path.isdir(ascatFile):
                    raise NotImplementedError(
                        'Option "-f" should be a directory when analyzing \
Sequenza results but found "%s"' % ascatFile)
                paramList = [RunSequenza(self.__binDir).
                             _createAscatFileFromSegmentFiles, ascatFile]
                ascatFile = \
                    Utilities.getFunctionResultWithCache(dumpFileName,
                                                         *paramList)
            else:
                paramList = [RunAscat(self.__binDir, self.__rLibDir).process,
                             ascatFile, self.__sampleFile,
                             self.__sampleAliasFile, gcFile, platform,
                             libDir, gw6Dir, snpFile, normalize, sampleList, targetDir]
                ascatFile = Utilities.getFunctionResultWithCache(dumpFileName,
                                                                 *paramList)
                # raise NotImplementedError('Supported fileType are ("ASCAT",
                # "Sequenza")')
        if not targetDir:
            targetDir = os.path.dirname(ascatFile)
        if not ploidyFile:
            ploidyFile = aCNViewer(None, 10, self.__binDir, self.__useShape,
                                   self.__sampleFile, self.__sampleAliasFile,
                                   self.__groupColumnName)._createPloidyFile(
                                       ascatFile, chrFile, targetDir, None,
                                       centromereFile, mergeCentromereSegments)
            # ploidyDict = self._getPloidyDictFromFile(ploidyFile)
            print 'Using ploidyFile = %s' % ploidyFile
        # add parameters for Illumina SNP arrays
        self.__ascatFile = ascatFile
        currentCentromereFile = centromereFile
        if keepCentromereData:
            currentCentromereFile = None
        outFileName, matrixDict, ploidyDict, chrSizeDict, centromereDict = \
            self.__createMatrixFileAndGetDicts(ascatFile, chrFile,
                                               targetDir, ploidyFile,
                                               currentCentromereFile,
                                               mergeCentromereSegments)
        # sys.exit(1)
        print ploidyDict
        try:
            groupDict, group2Dict, colorDict, shapeDict, shapeDict2 = \
                self._getGroupAndColorCodeDictFromFile(ploidyFile,
                                                       defaultGroupValue)
        except NotImplementedError:
            groupDict = {}
            colorDict = shapeDict = group2Dict = shapeDict2 = None
        # sys.exit(1)
        if plotAll or histogram:
            lohFileName = ascatFile + '_lohNeutral.txt'
            if targetDir:
                lohFileName = os.path.join(targetDir, os.path.basename(lohFileName))
            if not os.path.isfile(lohFileName):
                self._createLOHneutralSegments(ascatFile, ploidyFile, targetDir = targetDir)
            lohFileName2 = ascatFile + '_loh.txt'
            if targetDir:
                lohFileName2 = os.path.join(targetDir, os.path.basename(lohFileName2))
            if not os.path.isfile(lohFileName2):
                self._createLOH_Segments(ascatFile, ploidyFile, targetDir = targetDir)
            self.__sampleName = None
            self.__chrName = None
            currentCentromereDict = centromereDict
            if keepCentromereData:
                currentCentromereDict = None
            if keepCentromereData is None:
                keepCentromereData = False
            dumpFileName = lohFileName + '_%s_%d.pyDump5' % \
                (self.__suffix, keepCentromereData)
            if targetDir:
                dumpFileName = os.path.join(targetDir, os.path.basename(dumpFileName))
            paramList = [self.__createSegmentPloidyFile, lohFileName,
                         chrSizeDict, targetDir,
                         dict([[sampleName, 0] for sampleName in ploidyDict]),
                         currentCentromereDict]
            lohOutFileName, lohMatrixDict = \
                Utilities.getFunctionResultWithCache(dumpFileName,
                                                     *paramList)
            dumpFileName = lohFileName2 + '_%s_%d.pyDump5' % \
                (self.__suffix, keepCentromereData)
            if targetDir:
                dumpFileName = os.path.join(targetDir, os.path.basename(dumpFileName))
            paramList = [self.__createSegmentPloidyFile, lohFileName2,
                         chrSizeDict, targetDir,
                         dict([[sampleName, 0] for sampleName in ploidyDict]),
                         currentCentromereDict]
            lohOutFileName2, lohMatrixDict2 = \
                Utilities.getFunctionResultWithCache(dumpFileName,
                                                     *paramList)
            # matrixFile = self.__createMatrixFile(lohMatrixDict, targetDir,
            # None, None)
            if plotAll or merge:
                self.__createMergedHistograms(matrixDict, ploidyDict,
                                              targetDir, group2Dict,
                                              lohMatrixDict, groupDict,
                                              currentCentromereDict,
                                              lohMatrixDict2, lohToPlot)
            if _isCustom and plotAll or not merge:
                self.__createHistogramBySample(
                    matrixDict, ploidyDict, targetDir, lohMatrixDict,
                    currentCentromereDict)
        if plotAll or dendrogram or heatmap:
            matrixFile = self.__createMatrixFile(
                matrixDict, targetDir, None, ploidyFile)
        if plotAll or dendrogram:
            cluster = ThreadManager(_getNbAvailableCpus())
            if not self.__groupColumnName and _isCustom:
                colorDict['SIET'] = 'black'
            ploidyFile2 = ploidyFile + '_reordered.txt'
            ploidyFile3 = ploidyFile2 + '2'
            Utilities()._runFunc(self.__createReorderedPhenotypeFile, [
                ploidyFile, ploidyFile2, colorDict], ploidyFile2)
            cluster.submit(self._createDendrogram, matrixFile, groupDict,
                           colorDict, shapeDict, ploidyFile3, shapeDict2,
                           0.01269454, keyword, None, hclust)
            if not self.__groupColumnName and _isCustom:
                coeff = 0.02296309
                currentMatrixDict = self.__getSubsetMatrixForSamples(
                    matrixDict, groupDict.getall('SIET'))
                matrixFile = self.__createMatrixFile(
                    currentMatrixDict, targetDir, 'SIET')
                currentPhenotypeFile = ploidyFile + '_SIET.txt'
                paramList = [self.__createReorderedPhenotypeFileFromMatrixFile,
                             [matrixFile, ploidyFile, currentPhenotypeFile],
                             currentPhenotypeFile]
                Utilities()._runFunc(*paramList)
                cluster.submit(self._createDendrogram, matrixFile, groupDict,
                               colorDict, shapeDict, currentPhenotypeFile,
                               shapeDict2, coeff, None, None, hclust)

                currentMatrixDict = \
                    dict([[sampleName,
                           matrixDict[sampleName.split('_')[0]]]
                          for groupName in set(
                        groupDict.keys()) - set(['SIET'])
                        for sampleName in
                        groupDict.getall(groupName)])
                matrixFile = self.__createMatrixFile(
                    currentMatrixDict, targetDir, 'PET')
                currentPhenotypeFile = ploidyFile + '_PET.txt'
                paramList = [self.__createReorderedPhenotypeFileFromMatrixFile,
                             [matrixFile, ploidyFile, currentPhenotypeFile],
                             currentPhenotypeFile]
                Utilities()._runFunc(*paramList)
                cluster.submit(self._createDendrogram, matrixFile, groupDict,
                               colorDict, shapeDict, currentPhenotypeFile,
                               shapeDict2, coeff, None, None, hclust)
            cluster.wait()
        if plotAll or heatmap:
            self._createHeatmap(matrixFile, hclust, height, width, cexRow,
                                cexCol, margins, labRow, labCol, groupDict,
                                groupLegendPos, chrLegendPos)
        self.__cleanDir(targetDir)


class SubcommandHelpFormatter(argparse.RawDescriptionHelpFormatter):

    def _format_action(self, action):
        parts = super(argparse.RawDescriptionHelpFormatter,
                      self)._format_action(action)
        if action.nargs == argparse.PARSER:
            parts = "\n".join(parts.split("\n")[1:])
        return parts


class DefaultHelpParser(argparse.ArgumentParser):

    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        sys.exit(2)


def run(options, args):
    # def main():
    commonCommandParameterList = []
    description = 'aCNViewer is available at https://github.com/FJD-CEPH/\
aCNViewer and allows the visualization of absolute CNVs in a group of samples \
as stacked histograms for an easy dectection of recurrent CNVs, as heatmaps to\
 identify regions and samples sharing similar global CNV patterns and as \
dendrograms for identification of sample clusters.'
    parser = DefaultHelpParser(prog=__file__, formatter_class=lambda prog:
                               SubcommandHelpFormatter(prog,
                                                       max_help_position=20,
                                                       width=75),
                               description='\naCNViewer: comprehensive \
genome-wide visualization of absolute copy number and copy neutral \
variations\n\n%s\n' % ('\n'.join(textwrap.wrap(description, 100))),
        usage='aCNViewer.py command [options]',
        epilog='This is version {0} - Victor RENAULT - {1} - \
Contact: aCNViewer@cephb.fr'.format(0.1, '20161010'))
    subparsers = parser.add_subparsers(dest='module')
    subparsers.metavar = None

    if options.all:
        aCNViewer(options.windowSize, options.percentage, options.binDir,
                  options.useShape, options.sampleFile,
                  options.sampleAliasFile, options.groupColumnName).\
            processAll(options.fileName, options.chrFile,
                       options.targetDir, options.ploidyFile,
                       options.percentList, options.baseList,
                       options.histogram, options.merge,
                       options.dendrogram, options.plotAll,
                       options.centromereFile)
    elif options.progName == 'ASCAT':
        RunAscat(options.binDir, options.rLibDir).\
            process(options.fileName, options.sampleFile,
                    options.sampleAliasFile, options.gcFile,
                    options.platform, options.libDir, options.gw6Dir,
                    options.probeFile, options.normalize,
                    options.sampleList, options.targetDir)
    # elif options.progName == 'convertIlluminaReportsToLrrBaf':
        # RunAscat()._createMergedIlluminaFinalReports(fileList,
        # options.probeFile, options.outFileName, sampleList = None)
    elif options.progName == 'createFileWithUpdatedPositions':
        RunAscat(options.binDir)._createFileWithUpdatedPositions(
            options.fileName, options.probeFile, options.targetBuild)
    elif options.progName == 'dendroFeatures':
        RunAscat(options.binDir)._createDendrogramForEachFeature(
            options.fileName, options.targetDir, options.windowSize,
            options.percentage, options.fileName2, options.chrFile)
    elif options.progName == 'gc':
        RunSequenza(options.binDir)._createGcFile(options.refFileName)
    elif options.progName == 'liftOver':
        RunAscat(options.binDir)._liftOverRawProbeFile(
            options.fileName, options.targetBuild)
    elif options.progName == 'merge':
        RunAscat(options.binDir)._mergePloidyFileWithSampleInfoFile(
            options.fileName, options.sampleFile, options.sampleAliasFile)
    elif options.progName == 'ploidy2':
        aCNViewer(options.windowSize, options.percentage, options.binDir,
                  options.useShape, options.sampleFile,
                  options.sampleAliasFile, options.groupColumnName
                  )._createPloidyFile(options.fileName, options.chrFile,
                                      options.targetDir, None,
                                      options.centromereFile,
                                      options.mergeCentromereSegments,
                                      options.ploidyFile)
        # aCNViewer(options.windowSize, options.percentage, options.binDir,
        # options.useShape)._createPloidyFile2(options.fileName,
        # options.ploidyFile)
    elif options.progName == 'sequenza':
        RunSequenza(options.binDir, nbCpus=options.nbCpus,
                    memory=options.memory).process(options.sampleFile,
                                                   options.dirName,
                                                   options.pattern,
                                                   options.targetDir,
                                                   options.refFileName,
                                                   options.createMpileUp,
                                                   options.byChr)
    elif options.progName == 'seqz':
        RunSequenza(options.binDir, nbCpus=options.nbCpus)._createSeqzFile(
            options.tumorBam, options.normalBam, options.refFileName,
            options.gcFile, options.targetDir, options.createMpileUp,
            options.byChr)
    elif options.progName == 'seqzR':
        RunSequenza(options.binDir)._runSequenza(options.fileName,
                                                 options.chrName)
    else:
        aCNViewer(options.windowSize, options.percentage, options.binDir,
                  options.useShape, options.sampleFile,
                  options.sampleAliasFile, options.groupColumnName,
                  options.rLibDir, options.rColorFile).process(
                      options.fileName, options.chrFile, options.targetDir,
                      options.ploidyFile, options.histogram, options.merge,
                      options.dendrogram, options.plotAll,
                      options.centromereFile,
                      mergeCentromereSegments=options.mergeCentromereSegments,
                      gcFile=options.gcFile, platform=options.platform,
                      libDir=options.libDir, gw6Dir=options.gw6Dir,
                      snpFile=options.probeFile, normalize=options.normalize,
                      sampleList=options.sampleList, heatmap=options.heatmap,
                      hclust=options.hclust, height=options.height,
                      width=options.width, cexRow=options.cexRow,
                      cexCol=options.cexCol, margins=options.margins,
                      labRow=options.labRow, labCol=options.labCol,
                      groupLegendPos=options.groupLegendPos,
                      chrLegendPos=options.chrLegendPos,
                      fileType=options.fileType,
                      keepCentromereData=options.keepCentromereData,
                      lohToPlot=options.lohToPlot)

runFromTerminal(__name__, [CommandParameter('a', 'all',
                                            CommandParameterType.BOOLEAN,
                                            helpString='Set to True in order \
to generate plots using different resolutions in base pairs or in percentage \
of chromosome length'),

                           CommandParameter('b', 'binDir', 'string',
                                            helpString='Set the location of \
the binaries. If R is installed in a custom folder, symbolic links to "R" and \
"Rscript" should be created in binDir. If you plan to analyze Affymetrix CEL \
files, a link to APT (Affymetrix Power Tools) root folder should be created \
in binDir so the structure should be binDir/APT/bin'),

                           CommandParameter('B', 'baseList',
                                            CommandParameterType.COMMA_SEP_ID,
                                            defaultValue=[int(nb * 1000000)
                                                          for nb in [0.1, 0.5,
                                                                     1, 2, 5,
                                                                     10, 20]],
                                            helpString='List of segments size \
in base pairs used to split chromosomes for CNV matrix'),

                           CommandParameter('byChr',
                                            CommandParameterType.BOOLEAN,
                                            helpString='Sequenza parameter \
indicating wheter seqz file should be created by chromosome or not'),

                           CommandParameter('c', 'chrFile', 'string',
                                            helpString='A tab-delimited file \
with 2 columns respectively chromosome name and chromosome length'),

                           CommandParameter('C', 'centromereFile', 'string',
                                            helpString='File giving the \
centromere bounds. Can be generated using "curl -s "http://hgdownload.cse.ucsc\
.edu/goldenPath/BUILD/database/cytoBand.txt.gz" | gunzip -c | grep acen > \
centro_build.txt"'),

                           CommandParameter('cexCol', 'float',
                                            helpString='Set cexCol for \
heatmaps. See R heatmap.2 documentation for more details'),

                           CommandParameter('cexRow', 'float',
                                            helpString='Set cexRow for \
heatmaps. See R heatmap.2 documentation for more details'),

                           CommandParameter('chrLegendPos', 'string',
                                            defaultValue='bottomleft',
                                            helpString='Heatmap parameter to \
set the position of the chromosome color legend. The default value is \
"bottomleft" and can be changed to coordinates (for example "0.1,0.5") or in \
R specified logical location ("top","bottom", "left", "right", etc)'),

                           CommandParameter('chrName', 'string',
                                            helpString='Sequenza parameter \
indicating the name of the chromosome to process'),

                           CommandParameter('createMpileUp',
                                            CommandParameterType.BOOLEAN,
                                            helpString='Sequenza parameter \
used to indicate whether an intermediary mpileup file should be created'),

                           CommandParameter('d', 'dendrogram',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=False,
                                            helpString='if True, plot \
dendrograms'),

                           CommandParameter('D', 'dirName', 'string',
                                            helpString='Sequenza parameter \
indicating bam folder'),

                           CommandParameter('f', 'fileName', 'string'),

                           CommandParameter('fileName2', 'string'),

                           CommandParameter('fileType', 'string'),

                           CommandParameter('g', 'gcFile', 'string',
                                            helpString='GC file necessary for \
ASCAT GC correction when analyzing SNP array data. Please check ASCAT website \
for available GC files: https://www.crick.ac.uk/peter-van-loo/software/ASCAT'),

                           CommandParameter('G', 'groupColumnName', 'string',
                                            helpString='Name of the column in \
sampleFile used to separate samples into groups. If not set, plots will be \
generated on each feature specified in sampleFile'),

                           CommandParameter('groupLegendPos', 'string',
                                            defaultValue='topright',
                                            helpString='Heatmap parameter to \
set the position of the group color legend. The default value is "bottomleft" \
and can be changed to coordinates (for example "0.1,0.5") or in R specified \
logical location ("top","bottom", "left", "right", etc)'),

                           CommandParameter('gw6Dir', 'string',
                                            helpString='Affymetrix SNP array \
parameter indicating where http://www.openbioinformatics.org/penncnv/download/\
gw6.tar.gz has been uncompressed into. This archive contains different \
programs and files necessary to process Affymetrix SNP array'),

                           CommandParameter('hclust', 'string',
                                            helpString='Set hclust value for \
heatmaps'),

                           CommandParameter('heatmap',
                                            CommandParameterType.BOOLEAN,
                                            helpString='if True, plot \
heatmaps'),

                           CommandParameter('height', 'int',
                                            helpString='Set heatmap\'s \
height'),

                           CommandParameter('histogram',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=False,
                                            helpString='if True, plot \
histograms'),

                           CommandParameter('keepCentromereData',
                                            CommandParameterType.BOOLEAN),

                           CommandParameter('l', 'libDir', 'string',
                                            helpString='Affymetrix library \
file downloadable from Affymetrix website: \
http://www.affymetrix.com/support/technical/byproduct.affx?cat=dnaarrays'),

                           CommandParameter('labCol',
                                            CommandParameterType.BOOLEAN,
                                            helpString='If True, show sample \
labels in heatmaps'),

                           CommandParameter('labRow',
                                            CommandParameterType.BOOLEAN,
                                            helpString='If True, show \
position of chromosome segments in heatmaps'),

                           CommandParameter('lohToPlot', 'string',
                                            helpString='Histogram option for \
LOH plotting. Values should be one of "cn-LOH" for plotting cn-LOH only, \
"LOH" for LOH only or "both" for cn-LOH and LOH. The default value is \
"cn-LOH"'),

                           CommandParameter('m', 'merge',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=True),

                           CommandParameter('M', 'mergeCentromereSegments',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=False),

                           CommandParameter('margins',
                                            CommandParameterType.COMMA_SEP),

                           CommandParameter('memory', 'int', defaultValue=8,
                                            helpString='memory allocated to \
Sequenza in GB'),

                           CommandParameter('N', 'normalize',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=True),

                           CommandParameter('normalBam', 'string',
                                            helpString='Sequenza parameter \
indicating normal bam file'),

                           CommandParameter('n', 'nbCpus', 'int',
                                            helpString='Sequenza parameter \
indicating the number of threads to use when generating seqz file by \
chromosome'),

                           CommandParameter('o', 'outFileName', 'string'),

                           CommandParameter('p', 'percentage', 'float',
                                            helpString='Segment size in \
percentage of chromosome length used to split chromosomes for CNV matrix'),

                           CommandParameter('P', 'progName', 'string'),

                           CommandParameter('pattern', 'string',
                                            defaultValue='.bam',
                                            helpString='Sequenza parameter \
specifying bam file pattern'),

                           CommandParameter('percentList',
                                            CommandParameterType.
                                            COMMA_SEP_FLOAT,
                                            helpString='List of segments size \
in percentage of chromosome length used to split chromosomes for CNV \
matrix'),  # defaultValue=[0.5, 1, 2, 5, 10]),

                           CommandParameter('platform', 'string',
                                            helpString='Name of the SNP array \
platform used to generate the data to analyze. Currently supported values are \
"Affy250k_sty", "Affy250k_nsp", "Affy500k", "AffySNP6", "Illumina660k" and \
"HumanOmniExpress12"'),

                           CommandParameter('ploidyFile', 'string',
                                            helpString='Use ploidies from \
tab-delimited file with at least 2 columns: "sample" and "ploidy"'),

                           CommandParameter('plotAll',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=False,
                                            helpString='If True, plot \
histograms, heatmaps and dendrograms'),

                           CommandParameter('probeFile', 'string'),

                           CommandParameter('r', 'refFileName', 'string',
                                            helpString='Sequenza parameter \
indicating path to the reference file used to generate the bams'),

                           CommandParameter('rColorFile', 'string',
                                            helpString='Colors in histograms \
(section "[histogram]"), dendrograms (section "[group]") and heatmaps \
(sections "[chr]", "[group]" and "[heatmap]") can be redefined in that file. \
See tutorial on github for an example'),

                           CommandParameter('rLibDir', 'string',
                                            helpString='Set custom R library \
folder for installation of missing packages'),

                           CommandParameter('sampleAliasFile', 'string'),

                           CommandParameter('sampleFile', 'string',
                                            helpString='Tab-delimited file \
with clinical information with sample name "Sample" column'),

                           CommandParameter('sampleList',
                                            CommandParameterType.COMMA_SEP),

                           CommandParameter('t', 'targetDir', 'string',
                                            helpString='Set the location of \
the output folder'),

                           CommandParameter('T', 'targetBuild', 'string'),

                           CommandParameter('tumorBam', 'string',
                                            helpString='Sequenza parameter \
indicating tumor bam file'),

                           CommandParameter('u', 'useShape',
                                            CommandParameterType.BOOLEAN,
                                            defaultValue=False,
                                            helpString='When "dendrogram" or \
"plotAll" is True, replace sample labels with colored shapes representing \
each sample group'),

                           CommandParameter('w', 'windowSize', 'int',
                                            helpString='Segment size in base \
pairs used to split chromosomes for CNV matrix'),

                           CommandParameter('W', 'width', 'int',
                                            helpString='Set heatmap\'s \
width')], run)
