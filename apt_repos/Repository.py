#!/usr/bin/python3 -Es
# -*- coding: utf-8 -*-
##################################################################################
# Access information about binary and source packages in multiple
# (independent) apt-repositories utilizing libapt / python-apt/
# apt_pkg without the need to change the local system and it's apt-setup.
#
# Copyright (C) 2018  Christoph Lutz
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
##################################################################################
import os
import sys
import logging
import re
import json

import apt_pkg
import apt.progress
import functools

from apt_repos.RepositoryScanner import scanRepository
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)


class Repository:
    '''
        This class represents a Repository as descibed by an element of a .repos config file.
        A repository is able to scan the apt-repository automatically for it's suites and to
        dynamically create corresponding .suites-configuration for existing suites.
    '''

    def __init__(self, repoDesc):
        '''
            Creates a new Repository Object for the provided Repo Desciption repoDesc
            which is one entry of a .repos file.
        '''
        self.desc = repoDesc.get('Repository')
        self.prefix = repoDesc['Prefix']
        self.prefix = self.prefix + ('' if ':' in self.prefix else ':')
        self.commonTags = repoDesc.get('Tags', list())
        self.commonUrl = repoDesc['Url'] + ("/" if not repoDesc['Url'].endswith("/") else "")
        self.commonCodename = repoDesc.get('Codename')
        self.scan = repoDesc.get('Scan')
        self.extractSuiteFromReleaseUrl = repoDesc.get('ExtractSuiteFromReleaseUrl')
        self.suites = repoDesc.get("Suites", list())
        # convert self.suite string entries into dicts
        for x in range(len(self.suites)):
            s = self.suites[x]
            if isinstance(s, str):
                d = dict()
                d["Suite"] = s
                self.suites[x] = d
        self.architectures = repoDesc.get('Architectures')
        self.components = repoDesc.get('Components')
        self.trustedGPGFile = repoDesc.get('TrustedGPG')
        self.debSrc = repoDesc.get('DebSrc')
        self.trusted = repoDesc.get('Trusted')


    def querySuiteDescs(self, selRepo, selSuite):
        res = list()
        (unused_ownRepo, ownSuitePrefix) = self.prefix.split(":", 1)

        selSuite = ownSuitePrefix if selSuite=='' else selSuite
        if not selSuite.startswith(ownSuitePrefix):
            return res
        suite = selSuite[len(ownSuitePrefix):]

        first = True
        for suiteDict in self.suites:
            ownSuite = suiteDict["Suite"]
            if ownSuite.startswith("---"):
                continue
            if not self.__isRepositorySelected(selRepo, suiteDict):
                continue
            url = self.__getUrl(suiteDict)
            if url.startswith("file:"):
                expandedUrl = url.format(PWD=os.getcwd().replace(" ", "%20"))
                if expandedUrl != url:
                    logger.debug("Expanding URL to '{}'".format(expandedUrl))
                    url = expandedUrl
            if suite == ownSuite or suite=='':
                if first:
                    logger.info("Scanning {}".format(self))
                    first = False
                found = self.__getSelfContainedSuiteDefinition(url, ownSuite)
                if len(found) > 0:
                    logger.debug("Using self contained suite definition '{}' from the .repos file (no scan required)".format(ownSuite))
                else:
                    found = scanRepository(url, [self.__getCodename(suiteDict)])
                res.extend(self.__getSuiteDescs(self.prefix, found, suiteDict))
        
        if self.scan and self.__isRepositorySelected(selRepo):
            logger.info("Scanning {}".format(self))
            if len(suite) > 0:
                found = scanRepository(self.__getUrl(), [suite])
                res.extend(self.__getSuiteDescs(self.prefix, found))
            else:
                found = scanRepository(self.__getUrl())
                res.extend(self.__getSuiteDescs(self.prefix, found))
                
        return res


    def __isRepositorySelected(self, selRepo, suiteDict=dict()):
        '''
            Returns true if the repository is selected by the repository selector
            selRepo (which ist the part of the selector before ":", without ":").
            This method also respects Tags defined in the two levels "repository-
            common tags" and "suite specific tags" (see self.getTags(suite)).
        '''
        (ownRepo, _) = self.prefix.split(":", 1)
        validRepos = ['', ownRepo]
        validRepos.extend(sorted(self.__getTags(suiteDict)))
        return selRepo in validRepos


    def __getSelfContainedSuiteDefinition(self, url, suite):
        '''
            If all parameters needed to specify a suite are provided within the
            .repos-definition, a failing suiteScan can be mitigated by taking the
            provided parameters as suite specification.
        '''
        res = list()
        if suite and self.components and self.architectures and self.debSrc != None:
            res.append({
                        'repoUrl': url,
                        'releaseUrl': urljoin(url, os.path.join("dists", suite, "Release")),
                        'suite': suite,
                        'codename': suite,
                        'components': self.components,
                        'architectures': self.architectures,
                        'hasSources': self.debSrc
            })
        return res


    def __getSuiteDescs(self, prefix, suites, suiteDict=dict()):
        res = list()
        for suite in suites:
            try:
                archs = suite['architectures']
                if self.architectures:
                    archs = list()
                    for arch in self.architectures:
                        if arch in suite['architectures']:
                            archs.append(arch)
                components = suite['components']
                if self.components:
                    components = list()
                    for component in self.components:
                        if component in suite['components']:
                            components.append(component)
                suitename = suite['suite']
                if "Suite" in suiteDict:
                    suitename = suiteDict['Suite']
                suitenameFromReleaseUrl = re.sub(r".*/dists/", "", os.path.dirname(urlparse(suite['releaseUrl']).path))
                if self.extractSuiteFromReleaseUrl:
                    suitename = suitenameFromReleaseUrl
                option = '[trusted=yes] ' if self.__getTrustedFlag(suiteDict) else ''
                debSrc = suite['hasSources'] if self.debSrc == None else self.debSrc
                tags = sorted(self.__getTags(suiteDict))
                res.append({
                    "Suite" : prefix + suitename,
                    "Description" : self.desc,
                    "Tags" : tags,
                    "SourcesList" : "deb {}{} {} {}".format(option, suite['repoUrl'], suitenameFromReleaseUrl, " ".join(components)),
                    "DebSrc" : debSrc,
                    "Architectures" : archs,
                    "TrustedGPG" : self.trustedGPGFile
                })
            except Exception as e:
                logger.warn("Could not get Suite-Description for suite {}: {}".format(suite, e))
        return res


    def getArchitectures(self):
        '''
            Returns the architectures, the suite is configured for
        '''
        return self.architectures


    def getDescription(self):
        '''
            Returns the description of the Repository which is the content
            of the "Repository" key in the .repos description.
        ''' 
        return self.desc


    def __getTags(self, suiteDict=dict()):
        '''
            Returns a union of the common `Tags` value and suite the specific Tags
            (in suiteDict). If suiteDict is not given this method just returns the common Tags.
        '''
        tags = set(self.commonTags)
        tags = tags.union(set(suiteDict.get('Tags', list())))
        return tags
    

    def __getCodename(self, suiteDict=dict()):
        '''
            Returns the codename that represents the directory under the `dists` folder in which
            we should scan for a particular suite described by `suiteDict`. The codename is:
            the value to the key `Codename` in suiteDict if available, otherwise
            the value to the key `Codename` in the repo_description if available, otherwise
            the value to the key `Suite` in the suiteDict.
        '''
        if "Codename" in suiteDict:
            return suiteDict['Codename']
        elif self.commonCodename:
            return self.commonCodename
        else:
            return suiteDict['Suite']


    def __getTrustedFlag(self, suiteDict=dict()):
        '''
            Returns the 'Trusted'-flag that controls if "[trusted=yes]" appendix should
            be generated in the generated SuiteDesc(s).
            The 'Trusted'-flag is:
            the value to the key `Trusted` in suiteDict if available, otherwise
            the value to the key `Trusted` in the repo_description if available, otherwise `False`.
        '''
        if "Trusted" in suiteDict:
            return suiteDict['Trusted']
        elif self.trusted:
            return self.trusted
        else:
            return False


    def __getUrl(self, suiteDict=dict()):
        '''
            Returns joined version of the common `Url` value and suite the specific Url
            (in suiteDict). If suiteDict is not given this method just returns the common Url.
        '''
        return urljoin(self.commonUrl, suiteDict.get("Url", ''))


    def __str__(self):
        if self.desc:
            return "Repository '{}' ({})".format(self.desc, self.__getUrl())
        else:
            return "Repository {}".format(self.__getUrl())
